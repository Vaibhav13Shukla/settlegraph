import json

from settlegraph.engine.atomic_io import write_csv, write_json, write_text


def test_atomic_writers_publish_complete_files(tmp_path) -> None:
    text_path = tmp_path / "nested" / "audit.md"
    json_path = tmp_path / "nested" / "summary.json"
    csv_path = tmp_path / "nested" / "assignments.csv"

    write_text(text_path, "complete")
    write_json(json_path, {"status": "complete"})
    write_csv(csv_path, ["id", "label"], [{"id": "pay_1", "label": "AUTO_MATCH"}])

    assert text_path.read_text(encoding="utf-8") == "complete"
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"status": "complete"}
    assert csv_path.read_text(encoding="utf-8") == "id,label\npay_1,AUTO_MATCH\n"
    assert list(text_path.parent.glob(".*.tmp")) == []
