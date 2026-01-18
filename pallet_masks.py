"""
Pallet block allocation using 8-bit masks.
Represents a 4x2 grid (8 blocks) for pallet space allocation.

Grid layout (indices 0-7):
  ┌───┬───┬───┬───┐
  │ 4 │ 5 │ 6 │ 7 │  (back row)
  ├───┼───┼───┼───┤
  │ 0 │ 1 │ 2 │ 3 │  (front row)
  └───┴───┴───┴───┘
"""

BLOCK_PATTERNS = {
    1: [
        0b00000001,  # block 0
        0b00000010,  # block 1
        0b00000100,  # block 2
        0b00001000,  # block 3
        0b00010000,  # block 4
        0b00100000,  # block 5
        0b01000000,  # block 6
        0b10000000,  # block 7
    ],
    2: [
        0b00000011,  # blocks 0-1
        0b00000110,  # blocks 1-2
        0b00001100,  # blocks 2-3
        0b00110000,  # blocks 4-5
        0b01100000,  # blocks 5-6
        0b11000000,  # blocks 6-7
        0b00010001,  # blocks 0+4 (column)
        0b00100010,  # blocks 1+5 (column)
        0b01000100,  # blocks 2+6 (column)
        0b10001000,  # blocks 3+7 (column)
    ],
    3: [
        0b00000111,  # blocks 0-1-2
        0b00001110,  # blocks 1-2-3
        0b01110000,  # blocks 4-5-6
        0b11100000,  # blocks 5-6-7
    ],
    4: [
        0b00001111,  # front row
        0b11110000,  # back row
        0b00110011,  # left half
        0b11001100,  # right half
        0b00110110,  # center columns
    ],
    5: [
        0b00011111,  # front row + block 4
        0b00101111,  # front row + block 5
        0b01001111,  # front row + block 6
        0b10001111,  # front row + block 7
        0b11110001,  # back row + block 0
        0b11110010,  # back row + block 1
        0b11110100,  # back row + block 2
        0b11111000,  # back row + block 3
    ],
    6: [
        0b00111111,  # front + left back
        0b01011111,  # front + 4,6
        0b10011111,  # front + 4,7
        0b01101111,  # front + 5,6
        0b10101111,  # front + 5,7
        0b11001111,  # front + 6,7
        0b11110011,  # back + left front
        0b11110101,  # back + 0,2
        0b11110110,  # back + 1,2
        0b11111001,  # back + 0,3
        0b11111010,  # back + 1,3
        0b11111100,  # back + 2,3
    ],
    7: [
        0b01111111,  # all except block 7
        0b10111111,  # all except block 6
        0b11011111,  # all except block 5
        0b11101111,  # all except block 4
        0b11110111,  # all except block 3
        0b11111011,  # all except block 2
        0b11111101,  # all except block 1
        0b11111110,  # all except block 0
    ],
    8: [
        0b11111111,  # all blocks
    ],
}


def find_allocation(used_mask: int, blocks_needed: int) -> int | None:
    """
    Find a contiguous allocation pattern for the requested blocks.
    Returns the mask to allocate, or None if no fit.
    """
    if blocks_needed < 1 or blocks_needed > 8:
        return None

    patterns = BLOCK_PATTERNS.get(blocks_needed, [])

    for pattern in patterns:
        if (used_mask & pattern) == 0:
            return pattern

    return None


def count_used_blocks(mask: int) -> int:
    """Count how many blocks are used in a mask."""
    return bin(mask).count('1')


def count_free_blocks(used_mask: int) -> int:
    """Count how many blocks are free."""
    return 8 - count_used_blocks(used_mask)


def get_block_positions(mask: int) -> list[int]:
    """Get list of block indices that are set in the mask."""
    return [i for i in range(8) if mask & (1 << i)]


def mask_to_grid_display(mask: int) -> list[list[bool]]:
    """
    Convert mask to 2x4 grid for display.
    Returns [[row0], [row1]] where each row has 4 booleans.
    """
    front_row = [(mask & (1 << i)) != 0 for i in range(4)]
    back_row = [(mask & (1 << (i + 4))) != 0 for i in range(4)]
    return [front_row, back_row]
