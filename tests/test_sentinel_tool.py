"""Unit tests for sentinel_tool core modules, timelapse helpers, and serve.py."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from shapely.geometry import box, mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import importlib
import serve
import timelapse
from sentinel_tool.config import S2_CLOUD_SCL, utm_epsg
from sentinel_tool.indices import dn_to_reflectance, ndvi, ndwi, s2_dn_offset
from sentinel_tool.mosaic import composite, pick_scenes, stretch


def make_fake_item(
    item_id: str,
    bbox: tuple[float, float, float, float],
    *,
    cloud: float | None = 0.2,
    dt: datetime | None = None,
    baseline: str | None = None,
    epsg: int | None = None,
):
    props = {}
    if cloud is not None:
        props["eo:cloud_cover"] = cloud
    if baseline is not None:
        props["s2:processing_baseline"] = baseline
    if epsg is not None:
        props["proj:epsg"] = epsg
    return SimpleNamespace(
        id=item_id,
        bbox=list(bbox),
        geometry=mapping(box(*bbox)),
        properties=props,
        datetime=dt or datetime(2025, 1, 15, 5, 30, tzinfo=timezone.utc),
    )


class TestConfig(unittest.TestCase):
    def test_utm_epsg(self):
        self.assertEqual(utm_epsg(79.25, 11.0), 32644)    # Cauvery Delta
        self.assertEqual(utm_epsg(72.54, 23.05), 32643)   # Gandhinagar
        self.assertEqual(utm_epsg(151.2, -33.86), 32756)  # Sydney (South)
        self.assertEqual(utm_epsg(-180.0, 0.0), 32601)
        self.assertEqual(utm_epsg(180.0, 10.0), 32660)

    def test_scl_includes_nodata_saturated_and_cirrus(self):
        for cls in (0, 1, 3, 8, 9, 10):
            self.assertIn(cls, S2_CLOUD_SCL)


class TestIndicesAndCalibration(unittest.TestCase):
    def test_s2_dn_offset(self):
        old_item = make_fake_item("S2A_old", (0, 0, 1, 1), dt=datetime(2021, 6, 1, tzinfo=timezone.utc), baseline="03.01")
        new_item = make_fake_item("S2A_new", (0, 0, 1, 1), dt=datetime(2025, 6, 1, tzinfo=timezone.utc), baseline="05.11")
        reproc_item = make_fake_item("S2A_c1", (0, 0, 1, 1), dt=datetime(2019, 6, 1, tzinfo=timezone.utc), baseline="05.10")
        date_fallback_new = make_fake_item("S2A_dt", (0, 0, 1, 1), dt=datetime(2022, 2, 1, tzinfo=timezone.utc))
        date_fallback_old = make_fake_item("S2A_dt0", (0, 0, 1, 1), dt=datetime(2021, 12, 1, tzinfo=timezone.utc))

        self.assertEqual(s2_dn_offset(old_item), 0.0)
        self.assertEqual(s2_dn_offset(new_item), -1000.0)
        self.assertEqual(s2_dn_offset(reproc_item), -1000.0)
        self.assertEqual(s2_dn_offset(date_fallback_new), -1000.0)
        self.assertEqual(s2_dn_offset(date_fallback_old), 0.0)

    def test_dn_to_reflectance_masks_nodata_before_offset(self):
        raw = np.array([0, 1000, 3000], dtype=np.uint16)
        new_item = make_fake_item("S2A", (0, 0, 1, 1), baseline="05.09")
        old_item = make_fake_item("S2A", (0, 0, 1, 1), baseline="03.01")

        r_new = dn_to_reflectance(raw, item=new_item)
        self.assertTrue(np.isnan(r_new[0]))
        self.assertAlmostEqual(float(r_new[1]), 0.0, places=5)
        self.assertAlmostEqual(float(r_new[2]), 0.2, places=5)

        r_old = dn_to_reflectance(raw, item=old_item)
        self.assertTrue(np.isnan(r_old[0]))
        self.assertAlmostEqual(float(r_old[1]), 0.1, places=5)
        self.assertAlmostEqual(float(r_old[2]), 0.3, places=5)

    def test_ndvi_ndwi_guard_zero_and_negative_denominators(self):
        red = np.array([0.1, 0.0, -0.02, np.nan], dtype="float32")
        nir = np.array([0.5, 0.0, 0.01, 0.4], dtype="float32")
        out = ndvi(red, nir)
        self.assertAlmostEqual(float(out[0]), (0.5 - 0.1) / 0.6, places=5)
        self.assertTrue(np.isnan(out[1]))
        self.assertTrue(np.isnan(out[2]))  # denom = -0.01 <= 1e-6
        self.assertTrue(np.isnan(out[3]))

        water = ndwi(np.array([0.3], dtype="float32"), np.array([0.1], dtype="float32"))
        self.assertAlmostEqual(float(water[0]), 0.5, places=5)


class TestMosaic(unittest.TestCase):
    def test_pick_scenes_empty_and_fallback_coverage(self):
        aoi = (78.0, 27.0, 79.0, 28.0)
        self.assertEqual(pick_scenes([], aoi), ([], 0.0))

        cloudy = make_fake_item("cloudy", (78.0, 27.0, 78.5, 28.0), cloud=15.0)
        selected, cov = pick_scenes([cloudy], aoi, cloud_max=1.0)
        self.assertEqual(len(selected), 1)
        self.assertAlmostEqual(cov, 0.5, places=3)

    def test_pick_scenes_greedy_two_halves(self):
        aoi = (78.0, 27.0, 79.0, 28.0)
        left = make_fake_item("left", (77.5, 26.5, 78.5, 28.5), cloud=0.2)
        right = make_fake_item("right", (78.5, 26.5, 79.5, 28.5), cloud=0.3)
        selected, cov = pick_scenes([left, right], aoi, cloud_max=1.0)
        self.assertEqual(len(selected), 2)
        self.assertAlmostEqual(cov, 1.0, places=4)

    def test_composite_fills_nan_gaps(self):
        a = np.array([[[0.2, 0.2, 0.2], [np.nan, np.nan, np.nan]]], dtype="float32")
        b = np.array([[[0.9, 0.9, 0.9], [0.5, 0.4, 0.3]]], dtype="float32")
        out = composite([a, b])
        np.testing.assert_allclose(out[0, 0], [0.2, 0.2, 0.2])
        np.testing.assert_allclose(out[0, 1], [0.5, 0.4, 0.3])

    def test_stretch_joint_preserves_white_balance_and_handles_all_nan(self):
        # Uniformly warm image: red is twice blue everywhere -> joint stretch keeps red > blue.
        rgb = np.zeros((4, 4, 3), dtype="float32")
        rgb[..., 0] = np.linspace(0.2, 0.4, 16).reshape(4, 4)
        rgb[..., 1] = np.linspace(0.15, 0.3, 16).reshape(4, 4)
        rgb[..., 2] = np.linspace(0.1, 0.2, 16).reshape(4, 4)
        joint = stretch(rgb, low=0.0, high=100.0)
        self.assertGreater(int(joint[..., 0].mean()), int(joint[..., 2].mean()))

        empty = np.full((3, 3, 3), np.nan, dtype="float32")
        self.assertEqual(int(stretch(empty).max()), 0)

    def test_slugify_no_double_underscores(self):
        fake_stac = SimpleNamespace(search_items=lambda *a, **kw: [])
        with patch.dict(sys.modules, {"sentinel_tool.stac": fake_stac}):
            import make_cloudless

            importlib.reload(make_cloudless)
            self.assertEqual(make_cloudless.slugify("Agra - Taj Mahal"), "agra_taj_mahal")
            self.assertEqual(make_cloudless.slugify("Golden Temple, Amritsar"), "golden_temple_amritsar")


class TestMakeTilesChooseOptions(unittest.TestCase):
    def test_strict_cloud_filter_preserved(self):
        # Import make_tiles with astral/solar mocked if astral isn't installed in global Python.
        fake_solar = SimpleNamespace(
            solar_elevation=lambda item, lat, lon: float(item.properties.get("_elev", 45.0))
        )
        fake_stac = SimpleNamespace(search_items=lambda *a, **kw: [])
        fake_fulltile = SimpleNamespace(load_fulltile_rgb=lambda *a, **kw: None)
        with patch.dict(
            sys.modules,
            {
                "sentinel_tool.solar": fake_solar,
                "sentinel_tool.stac": fake_stac,
                "sentinel_tool.fulltile": fake_fulltile,
            },
        ):
            import make_tiles

            importlib.reload(make_tiles)

            full_bbox = (77.5, 26.5, 78.5, 27.5)
            partial_bbox = (77.9, 27.0, 78.2, 27.3)
            items = []
            # 3 strictly cloud-free (<0.5%) full-tile scenes
            for idx, (elev, cld) in enumerate([(62.0, 0.1), (48.0, 0.2), (35.0, 0.3)]):
                it = make_fake_item(f"S2A_MSIL2A_2025010{idx}_T43RGM_01", full_bbox, cloud=cld)
                it.properties["_elev"] = elev
                items.append(it)
            # 1 dirtier scene (1.9% cloud) with extreme elevation that must NOT override strict pool
            dirty = make_fake_item("S2A_MSIL2A_20250601_T43RGM_01", full_bbox, cloud=1.9)
            dirty.properties["_elev"] = 75.0
            items.append(dirty)
            # 1 partial swath scene that must be excluded
            partial = make_fake_item("S2A_MSIL2A_20250602_T43RGM_01", partial_bbox, cloud=0.0)
            partial.properties["_elev"] = 80.0
            items.append(partial)

            chosen = make_tiles.choose_options(items, 27.17, 78.04)
            self.assertEqual(len(chosen), 3)
            self.assertTrue(all(c[1] < 0.5 for c in chosen))
            self.assertEqual([c[0] for c in chosen], [62.0, 48.0, 35.0])


class TestTimelapseHelpers(unittest.TestCase):
    def test_frames_for_and_output_mp4_path(self):
        self.assertEqual(timelapse.frames_for("gandhinagar").name, "frames_gandhinagar")
        self.assertEqual(timelapse.frames_for("").name, "frames")

        files = [Path("2019-03-10.jpg"), Path("2023-11-22.jpg")]
        self.assertEqual(
            timelapse.output_mp4_path(Path("frames_gandhinagar"), files).name,
            "timelapse_gandhinagar_2019_2023.mp4",
        )
        self.assertEqual(
            timelapse.output_mp4_path(Path("frames"), files).name,
            "timelapse_2019_2023.mp4",
        )

    def test_tile_epsg_prefers_proj_epsg_and_handles_hemispheres(self):
        it_proj = make_fake_item("S2A_T43QGE_x", (72.0, 23.0, 73.0, 24.0), epsg=32642)
        self.assertEqual(timelapse.tile_epsg(it_proj), 32642)

        it_north = make_fake_item("S2A_20250101_T43QGE_x", (72.0, 23.0, 73.0, 24.0))
        self.assertEqual(timelapse.tile_epsg(it_north), 32643)

        it_south = make_fake_item("S2A_20250101_T56HLH_x", (151.0, -34.0, 152.0, -33.0))
        self.assertEqual(timelapse.tile_epsg(it_south), 32756)

    def test_write_concat_list_posix_and_final_frame_repeat(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            files = [d / "2020-01-01.jpg", d / "2020-01-06.jpg"]
            listfile = d / "frames.txt"
            timelapse.write_concat_list(files, listfile, fps=10.0)
            text = listfile.read_text(encoding="utf-8")
            self.assertNotIn("\\", text)
            lines = text.splitlines()
            self.assertEqual(
                lines,
                [
                    "file '2020-01-01.jpg'",
                    "duration 0.100000",
                    "file '2020-01-06.jpg'",
                    "duration 0.100000",
                    "file '2020-01-06.jpg'",
                ],
            )


class TestServeSecurityAndStatus(unittest.TestCase):
    def test_status_deduplicates_retried_dates(self):
        with tempfile.TemporaryDirectory() as td:
            frames = Path(td)
            (frames / "manifest.json").write_text(
                json.dumps({"entries": [{"date": f"2023-01-0{i}", "items": []} for i in range(1, 6)]}),
                encoding="utf-8",
            )
            # 2023-01-01 failed first, then succeeded on retry (and JPG exists)
            (frames / "2023-01-01.jpg").write_bytes(b"\xff\xd8\xff\xd9")
            (frames / "fetch_0.log").write_text(
                "\n".join(
                    [
                        "2023-01-01 ERROR TimeoutError: net",
                        "2023-01-01 OK missing=0.0% (8s) [1/5]",
                        "2023-01-02 DISCARD_CLOUD cloud=25.0%",
                        "2023-01-02 DISCARD_CLOUD cloud=25.0%",  # duplicate after restart
                        "2023-01-03 ERROR RuntimeError: transient",
                        "2023-01-03 ERROR RuntimeError: transient",  # duplicate after restart
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            s = serve.status(frames)
            self.assertEqual(s["total"], 5)
            self.assertEqual(s["done"], 1)
            self.assertEqual(s["discarded_cloud"], 1)
            self.assertEqual(s["errors"], 1)
            self.assertEqual(s["remaining"], 2)

    def test_http_server_blocks_traversal_and_repo_files(self):
        with tempfile.TemporaryDirectory() as td:
            frames = Path(td)
            img_path = frames / "2023-01-01.jpg"
            Image.new("RGB", (4, 4), "green").save(img_path)

            httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(frames))
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                base = f"http://127.0.0.1:{port}"

                # Allowed: player.html, /api/status, valid frame inside frames/
                with urllib.request.urlopen(f"{base}/player.html", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                with urllib.request.urlopen(f"{base}/api/status", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    data = json.loads(r.read())
                    self.assertEqual(data["frames"], ["2023-01-01"])
                with urllib.request.urlopen(f"{base}/frames/2023-01-01.jpg", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    self.assertIn("immutable", r.headers.get("Cache-Control", ""))

                # Blocked: traversal attempts and arbitrary repo files
                for bad_path, expected in [
                    ("/frames/%2e%2e/serve.py", 403),
                    ("/frames/..%5cserve.py", 403),
                    ("/serve.py", 404),
                    ("/.git/HEAD", 404),
                ]:
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(f"{base}{bad_path}", timeout=5)
                    self.assertEqual(ctx.exception.code, expected, msg=bad_path)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
