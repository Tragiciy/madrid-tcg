import pathlib
import unittest


class AggregatorWiringTests(unittest.TestCase):
    """main() must call every output stage — a missed hook silently freezes
    a published artefact (it happened with the SEO pages)."""

    def test_main_writes_every_output(self):
        src = pathlib.Path("aggregator.py").read_text(encoding="utf-8")
        main = src[src.index("def main()"):]
        for call in ("write_events(", "save_store_registry(", "write_api(build_api(",
                     "write_pages(build_pages(", "write_share_pages(build_share_pages(", "write_json_atomic(STATS_FILE", "append_source_runs("):
            self.assertIn(call, main, f"aggregator.main() no longer calls {call}")


if __name__ == "__main__":
    unittest.main()
