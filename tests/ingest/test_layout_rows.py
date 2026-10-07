from tuppence.ingest.layout_rows import Box, rows_from_boxes


def test_boxes_on_one_line_join_left_to_right_with_column_gaps():
    boxes = [
        Box("42.18", 500, 100, 530, 110),
        Box("Greenbasket", 150, 101, 210, 111),
        Box("Stores", 214, 100, 250, 110),
        Box("29 Sep 2026", 56, 100, 110, 110),
        Box("Little", 150, 120, 180, 130),
        Box("Cafe", 184, 121, 205, 131),
    ]
    assert rows_from_boxes(boxes) == ["29 Sep 2026   Greenbasket Stores   42.18", "Little Cafe"]


def test_no_boxes():
    assert rows_from_boxes([]) == []


def test_a_very_long_row_is_grouped_in_linear_time():
    import time

    boxes = [Box("w", i * 6.0, 100, i * 6.0 + 5, 110) for i in range(60_000)]
    started = time.monotonic()
    rows = rows_from_boxes(boxes)
    assert len(rows) == 1 and time.monotonic() - started < 5
