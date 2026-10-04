"""create/collage.py: layouts, spacing, cover fitting, swap."""
import pytest
from PIL import Image

from lunelis.create import collage as C


def solid(colors):
    return lambda fid, edge: Image.new("RGB", (400, 300), colors[fid])


def test_every_template_fits_inside_and_cells_never_overlap():
    for name, cells in C.TEMPLATES.items():
        opts = C.CollageOptions(template=name, aspect="4:5", spacing=2, border=3,
                                cells=[C.Cell(i + 1) for i in range(len(cells))])
        size = C.canvas_size(opts, 1000)
        boxes = C.cell_boxes(opts, size)
        assert len(boxes) == len(cells)
        for i, a in enumerate(boxes):
            assert 0 <= a[0] < a[2] <= size[0] and 0 <= a[1] < a[3] <= size[1], name
            for b in boxes[i + 1:]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], name


def test_aspects_and_the_border_and_gap_in_pixels():
    opts = C.CollageOptions(template="2 side by side", aspect="16:9", spacing=2, border=5,
                            cells=[C.Cell(1), C.Cell(2)])
    assert C.canvas_size(opts, 1600) == (1600, 900)
    left, right = C.cell_boxes(opts, (1600, 900))
    assert left[0] == 45 and left[1] == 45                  # 5 % of the 900 px short side
    assert right[0] - left[2] == 18                         # 2 % gap between the cells
    assert C.canvas_size(C.CollageOptions(aspect="9:16"), 1920) == (1080, 1920)


def test_render_fills_cells_with_the_background_between():
    colors = {1: "red", 2: "blue"}
    opts = C.CollageOptions(template="2 side by side", aspect="16:9", spacing=4, border=4,
                            background="#00ff00", cells=[C.Cell(1), C.Cell(2)])
    img = C.render(opts, solid(colors), long_edge=800)
    assert img.size == (800, 450)
    assert img.getpixel((5, 5)) == (0, 255, 0)              # border
    assert img.getpixel((200, 225))[0] > 200                # red cell
    assert img.getpixel((600, 225))[2] > 200                # blue cell
    assert img.getpixel((400, 225)) == (0, 255, 0)          # the gap


def test_cover_zoom_and_pan_pick_the_right_part():
    img = Image.new("RGB", (200, 100), "black")
    img.paste(Image.new("RGB", (100, 100), "white"), (100, 0))     # right half white
    assert C.cover(img, (50, 50), 1, (0.0, 0.5)).getpixel((25, 25)) == (0, 0, 0)
    assert C.cover(img, (50, 50), 1, (1.0, 0.5)).getpixel((25, 25)) == (255, 255, 255)
    assert C.cover(img, (50, 50), 4, (0.75, 0.5)).size == (50, 50)


def test_swap_moves_photos_not_cells_and_free_rects_render():
    opts = C.CollageOptions(template="1 big + 2", cells=[C.Cell(1, zoom=2), C.Cell(2), C.Cell(3)])
    s = C.swap(opts, 0, 2)
    assert [c.file_id for c in s.cells] == [3, 2, 1] and s.cells[2].zoom == 2
    free = C.CollageOptions(template="free", aspect="1:1", border=0, spacing=0,
                            cells=[C.Cell(1, rect=(0, 0, .3, .3)), C.Cell(2, rect=(.5, .5, .5, .5))])
    free.check()
    img = C.render(free, solid({1: "red", 2: "blue"}), long_edge=100)
    assert img.getpixel((10, 10))[0] > 200 and img.getpixel((80, 80))[2] > 200
    assert img.getpixel((40, 10)) == (255, 255, 255)


def test_checks():
    with pytest.raises(ValueError):
        C.CollageOptions(cells=[C.Cell()]).check()          # no photos
    with pytest.raises(ValueError):
        C.CollageOptions(aspect="5:7", cells=[C.Cell(1)]).check()
