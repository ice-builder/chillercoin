# 🛡️ ZeroChainAI

**0-Day Vulnerability Intelligence for Blockchain**

AI-powered vulnerability detection across every layer of the blockchain stack — from smart contracts to consensus protocols.

## 🎯 What We Do

| Layer | Analysis |
|-------|----------|
| L1 | Smart Contracts (Solidity, Rust, Move, Vyper) |
| L2 | Protocol Design (tokenomics, governance, bridges) |
| L3 | Infrastructure (keys, multisig, CI/CD, supply chain) |
| L4 | Consensus & Network (eclipse, sybil, validator) |
| L5 | Zero-Day Research (compiler, VM, cryptographic) |

## 🏗️ Project Structure

```
ZeroChainAI/
├── src/
│   ├── scanner/          # AI vulnerability scanner
│   ├── monitor/          # ZeroGuard — continuous monitoring
│   ├── api/              # FastAPI backend
│   └── datasets/         # Vulnerability training data
├── website/              # Landing page
├── models/               # Trained AI models
├── tests/                # Test suite
├── scripts/              # Utility scripts
├── configs/              # Configuration files
└── docs/                 # Documentation
```

## 🧠 Tech Stack

- **AI**: Qwen2.5-Coder (fine-tuned) + PyTorch Geometric (GNN)
- **Analysis**: Slither + Mythril + Semgrep + custom rules
- **Backend**: Python 3.12+ / FastAPI
- **Infra**: Docker, GPU instances for model inference

## 🚀 Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run scanner on a Solidity file
python -m src.scanner.analyze path/to/Contract.sol

# Start API server
uvicorn src.api.main:app --reload
```

## 📊 Products

- **ZeroScan** — Instant AI scan ($500+)
- **ZeroAudit** — Full protocol audit ($20K+)
- **ZeroGuard** — 24/7 monitoring ($5K/mo+)

---

© 2026 ZeroChainAI. All rights reserved.
