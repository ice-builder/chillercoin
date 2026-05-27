#!/usr/bin/env python3
"""Remove duplicate block from paper_trader.py"""

path = "/home/trader/soldier/paper_trader.py"
with open(path) as f:
    lines = f.readlines()

# Find the duplicate: first "else:" with "v8.3" comment, then another "else:" with "v8.3"
first_else_idx = None
second_else_idx = None

for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == "else:" and i + 1 < len(lines) and "v8.3" in lines[i+1]:
        if first_else_idx is None:
            first_else_idx = i
        elif second_else_idx is None:
            second_else_idx = i
            break

if first_else_idx is not None and second_else_idx is not None:
    print(f"Found duplicate: first at line {first_else_idx+1}, second at {second_else_idx+1}")
    # Delete from first_else_idx to second_else_idx-1 (the incomplete duplicate)
    count = second_else_idx - first_else_idx
    print(f"Deleting {count} lines ({first_else_idx+1} to {second_else_idx})")
    del lines[first_else_idx:second_else_idx]
    
    with open(path, "w") as f:
        f.writelines(lines)
    print("FIXED!")
    
    # Verify
    for j in range(first_else_idx-2, min(first_else_idx+15, len(lines))):
        print(f"  {j+1}: {lines[j]}", end="")
else:
    print(f"No duplicate found. first={first_else_idx}, second={second_else_idx}")
