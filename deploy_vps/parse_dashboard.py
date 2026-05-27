#!/usr/bin/env python3
"""Parse dashboard HTML to extract balance info."""
import sys, re

html = sys.stdin.read()

# Find all dollar values
vals = re.findall(r'\$[\d,.]+', html)
print(f"Dollar values: {vals[:10]}")

# Find PnL percentages  
pnls = re.findall(r'[+-]?[\d.]+%', html)
print(f"PnL values: {pnls[:10]}")

# Find Deposit references
deps = re.findall(r'Deposit:\s*\$[\d,.]+', html)
print(f"Deposits: {deps}")

# Balance card
bc = re.findall(r'balance-card[^>]*>.*?class=["\']value["\'][^>]*>([^<]+)', html, re.DOTALL)
print(f"Balance cards: {bc[:5]}")

# Soldier section
m = re.search(r'Soldier(.*?)Pump', html, re.DOTALL)
if m:
    section = m.group(1)
    s_vals = re.findall(r'\$[\d,.]+', section)
    s_pnl = re.findall(r'[+-]?[\d.]+%', section)
    print(f"\nSoldier section - Balances: {s_vals[:5]}, PnL: {s_pnl[:5]}")
