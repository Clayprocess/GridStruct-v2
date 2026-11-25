"""外框成功重分析的纯数据验收：人工校对保留，来源坐标随撤销完整恢复。"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from table.model import CONFIRMED, CellMerge, GridSpec, Rect, Token
from table.review import TableReview


def main():
    grid = GridSpec((0, 40, 80), (0, 100, 200))
    tokens = (Token('旧金额', .99, Rect(110, 10, 180, 30)),
              Token('待确认', .5, Rect(10, 50, 80, 70)))
    review = TableReview(tokens, grid, origin=(40, 60))
    review.edit(0, 1, '1,234.56')
    review.confirm(1, 0)
    corrected = review.document
    source_tokens = (Token('旧金额', .99, Rect(120, 20, 190, 40)),
                     Token('待确认', .5, Rect(20, 60, 90, 80)),
                     Token('新边缘', .99, Rect(210, 20, 230, 40)))
    source_grid = GridSpec((0, 50, 100), (0, 110, 200, 240))
    review.replace_source(source_tokens, source_grid, (30, 50))
    expanded = review.document
    assert expanded.cell(0, 1).text == '1,234.56'
    assert expanded.cell(1, 0).text == '待确认' and expanded.cell(1, 0).status == CONFIRMED
    assert expanded.cell(0, 1).corrections[0].box == Rect(110, 10, 210, 50)
    assert expanded.cell(1, 0).corrections[0].box == Rect(20, 60, 90, 80)
    assert expanded.cell(0, 2).text == '新边缘'
    review.undo()
    assert review.document == corrected and review.origin == (40, 60)
    assert review._tokens == tokens
    review.redo()
    assert review.document == expanded and review.origin == (30, 50)
    assert review._tokens == source_tokens
    review.undo()
    review.set_grid(GridSpec((0, 41, 80), (0, 101, 200)))
    assert '新边缘' not in str(review.document.rows), '撤销后重新分格必须用旧来源'
    assert review.document.cell(0, 1).text == '1,234.56'
    review.undo()
    assert review.document == corrected and review.origin == (40, 60)

    # 已合并格内的两条人工改字仍各守原位置，重分析后拆开不丢字。
    review = TableReview(tokens, grid, origin=(40, 60))
    review.edit(0, 0, '左修订')
    review.edit(0, 1, '右修订')
    review.set_grid(grid.with_merge(CellMerge(0, 0, 1, 2)))
    merged = review.document
    review.replace_source(source_tokens, source_grid, (30, 50))
    assert review.document.cell(0, 0).text == '左修订'
    assert review.document.cell(0, 1).text == '右修订'
    review.undo()
    assert review.document == merged

    # 先结清未松开的拖线，再记录重分析；人工删空也必须保持为空。
    review = TableReview(tokens, grid)
    review.edit(0, 1, '')
    review.set_grid(GridSpec((0, 42, 80), (0, 100, 200)), final=False)
    dragged = review.document
    review.replace_source(tokens, grid, (0, 0))
    assert review.document.cell(0, 1).text == ''
    review.undo()
    assert review.document == dragged
    review.undo()
    assert review.document.grid == grid and review.document.cell(0, 1).text == ''
    # 缩掉已编辑的行/列：边缘格不混入框外校正，撤销完整恢复被裁出的编辑。
    crop_tokens = (Token('左上', .99, Rect(10, 10, 80, 30)),
                   Token('右下', .99, Rect(110, 50, 180, 70)))
    for origin, cropped_grid, expected in (
        ((0, 40), GridSpec((0, 40), (0, 100, 200)), '右下修订'),
        ((100, 0), GridSpec((0, 40, 80), (0, 100)), '右下修订'),
        ((0, 0), GridSpec((0, 40), (0, 100, 200)), '左上修订'),
        ((0, 0), GridSpec((0, 40, 80), (0, 100)), '左上修订'),
    ):
        review = TableReview(crop_tokens, grid)
        review.edit(0, 0, '左上修订')
        review.edit(1, 1, '右下修订')
        before_crop = review.document
        shifted = []
        for token in crop_tokens:
            box = token.box
            moved = Rect(box.left - origin[0], box.top - origin[1],
                         box.right - origin[0], box.bottom - origin[1])
            if (0 <= moved.center_x < cropped_grid.vertical_lines[-1]
                    and 0 <= moved.center_y < cropped_grid.horizontal_lines[-1]):
                shifted.append(Token(token.text, token.score, moved))
        review.replace_source(shifted, cropped_grid, origin)
        assert [cell.text for row in review.document.cells for cell in row if cell.text] == [expected]
        after_crop = review.document
        review.undo()
        assert review.document == before_crop and review.origin == (0, 0) and review._tokens == crop_tokens
        review.redo()
        assert review.document == after_crop and review.origin == origin
    print('PASS: successful source replacement preserves edits/confirmations/deletions/merged corrections; undo/redo restore tokens and origin; interrupted drag settles separately; cropped edits do not leak into edge cells and remain undoable')


if __name__ == '__main__':
    main()
