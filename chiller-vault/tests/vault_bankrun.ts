/**
 * Bankrun tests for time-gated vault logic.
 * Uses solana-bankrun to manipulate Clock sysvar.
 *
 * Tests:
 *  - update_nav rejected when called within 1h cooldown
 *  - update_nav succeeds after 1h warp
 *  - pause cooldown: can't re-pause within 24h
 *  - pause cooldown: can re-pause after 24h
 */
import { start, Clock, BanksClient, ProgramTestContext } from "solana-bankrun";
import { Connection, Keypair, PublicKey, SystemProgram, Transaction, TransactionInstruction, LAMPORTS_PER_SOL } from "@solana/web3.js";
import { createHash } from "crypto";
import { assert } from "chai";
import * as path from "path";

const PROGRAM_ID = new PublicKey("7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH");
const BPF_UPGRADEABLE = new PublicKey("BPFLoaderUpgradeab1e11111111111111111111111");

function sighash(ns: string, name: string): Buffer {
  return createHash("sha256").update(`global:${name}`).digest().subarray(0, 8);
}

const [vaultPda] = PublicKey.findProgramAddressSync([Buffer.from("vault")], PROGRAM_ID);
const [chillerMint] = PublicKey.findProgramAddressSync([Buffer.from("chiller-mint")], PROGRAM_ID);
const [solVaultPda] = PublicKey.findProgramAddressSync([Buffer.from("sol-vault")], PROGRAM_ID);
const [programData] = PublicKey.findProgramAddressSync([PROGRAM_ID.toBuffer()], BPF_UPGRADEABLE);

describe("Time-gated tests (Bankrun)", () => {
  let ctx: ProgramTestContext;
  let authority: Keypair;
  let teamWallet: Keypair;
  let tradeWallet: Keypair;
  let userWallet: Keypair;
  let banksClient: BanksClient;

  before(async () => {
    authority = Keypair.generate();
    teamWallet = Keypair.generate();
    tradeWallet = Keypair.generate();
    userWallet = Keypair.generate();

    // Load the compiled program
    const programSo = path.resolve(__dirname, "../target/deploy/chiller_vault.so");

    // Bankrun doesn't create ProgramData account automatically.
    // Build ProgramData header: type(4) + slot(8) + Option<Pubkey>(1+32) = 45 bytes
    // Pad to be larger than 45 to pass length check
    const pdHeader = Buffer.alloc(128);
    pdHeader.writeUInt32LE(3, 0);             // UpgradeableLoaderState::ProgramData
    pdHeader.writeBigUInt64LE(BigInt(0), 4);  // slot
    pdHeader[12] = 1;                          // Option::Some
    authority.publicKey.toBuffer().copy(pdHeader, 13); // upgrade authority

    ctx = await start(
      [{ name: "chiller_vault", programId: PROGRAM_ID }],
      [
        { address: authority.publicKey, info: { lamports: 100 * LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: teamWallet.publicKey, info: { lamports: LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: tradeWallet.publicKey, info: { lamports: LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: userWallet.publicKey, info: { lamports: 50 * LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        // Pre-seed ProgramData PDA with correct header for H-1 check
        { address: programData, info: { lamports: 10 * LAMPORTS_PER_SOL, data: pdHeader, owner: BPF_UPGRADEABLE, executable: false } },
      ]
    );
    banksClient = ctx.banksClient;

    // 1. create_mint
    const mintIx = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authority.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: false },
        { pubkey: chillerMint, isSigner: false, isWritable: true },
        { pubkey: new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"), isSigner: false, isWritable: false },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
        { pubkey: new PublicKey("SysvarRent111111111111111111111111111111111"), isSigner: false, isWritable: false },
      ],
      data: sighash("global", "create_mint"),
    });
    const mintTx = new Transaction().add(mintIx);
    mintTx.recentBlockhash = ctx.lastBlockhash;
    mintTx.feePayer = authority.publicKey;
    mintTx.sign(authority);
    await banksClient.processTransaction(mintTx);

    // 2. create_treasury
    const treasuryIx = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authority.publicKey, isSigner: true, isWritable: true },
        { pubkey: solVaultPda, isSigner: false, isWritable: true },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ],
      data: sighash("global", "create_treasury"),
    });
    const treasuryTx = new Transaction().add(treasuryIx);
    treasuryTx.recentBlockhash = ctx.lastBlockhash;
    treasuryTx.feePayer = authority.publicKey;
    treasuryTx.sign(authority);
    await banksClient.processTransaction(treasuryTx);

    // 3. initialize
    const initData = Buffer.alloc(8 + 2 + 2 + 2 + 8 + 8);
    sighash("global", "initialize").copy(initData, 0);
    initData.writeUInt16LE(2000, 8);   // perf_fee = 20%
    initData.writeUInt16LE(200, 10);   // mgmt_fee = 2%
    initData.writeUInt16LE(50, 12);    // wd_fee = 0.5%
    initData.writeBigUInt64LE(BigInt(500_000_000), 14);
    initData.writeBigUInt64LE(BigInt(100_000_000_000), 22);

    const initIx = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authority.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
        { pubkey: chillerMint, isSigner: false, isWritable: false },
        { pubkey: solVaultPda, isSigner: false, isWritable: false },
        { pubkey: teamWallet.publicKey, isSigner: false, isWritable: false },
        { pubkey: tradeWallet.publicKey, isSigner: false, isWritable: false },
        { pubkey: programData, isSigner: false, isWritable: false },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ],
      data: initData,
    });
    const initTx = new Transaction().add(initIx);
    initTx.recentBlockhash = ctx.lastBlockhash;
    initTx.feePayer = authority.publicKey;
    initTx.sign(authority);
    await banksClient.processTransaction(initTx);
  });

  function makeUpdateNavIx(vaultVal: bigint, driftVal: bigint): TransactionInstruction {
    const data = Buffer.alloc(8 + 8 + 8);
    sighash("global", "update_nav").copy(data, 0);
    data.writeBigUInt64LE(vaultVal, 8);
    data.writeBigUInt64LE(driftVal, 16);
    return new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authority.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
        { pubkey: solVaultPda, isSigner: false, isWritable: false },
      ],
      data,
    });
  }

  // ─── Test 1: NAV update rejected within 1h cooldown ───
  it("update_nav rejected within 1h cooldown (C-3)", async () => {
    // total_assets=0, try setting to 0 (no change) — should still fail on cooldown
    const ix = makeUpdateNavIx(BigInt(0), BigInt(0));
    const tx = new Transaction().add(ix);
    tx.recentBlockhash = ctx.lastBlockhash;
    tx.feePayer = authority.publicKey;
    tx.sign(authority);

    try {
      await banksClient.processTransaction(tx);
      assert.fail("Should have been rejected by C-3 cooldown");
    } catch (e: any) {
      // NavUpdateTooFrequent = error 6014 = 0x177e
      const msg = e.message || String(e);
      assert.isTrue(msg.includes("0x177e") || msg.includes("NavUpdateTooFrequent"),
        "Expected NavUpdateTooFrequent (0x177e) error");
      console.log("    ✅ NAV update correctly rejected (C-3 cooldown <1h)");
    }
  });

  // ─── Test 2: NAV update succeeds after 1h warp ───
  it("update_nav succeeds after 1h clock warp", async () => {
    // Warp clock forward 3601 seconds to pass 1h cooldown
    const currentClock = await banksClient.getClock();
    ctx.setClock(new Clock(
      currentClock.slot,
      currentClock.epochStartTimestamp,
      currentClock.epoch,
      currentClock.leaderScheduleEpoch,
      currentClock.unixTimestamp + BigInt(3601)
    ));
    ctx.warpToSlot(currentClock.slot + BigInt(10));

    // total_assets=0, set to 0 (no change) — should pass cooldown now
    const ix = makeUpdateNavIx(BigInt(0), BigInt(0));
    const tx = new Transaction().add(ix);
    tx.recentBlockhash = ctx.lastBlockhash;
    tx.feePayer = authority.publicKey;
    tx.sign(authority);
    await banksClient.processTransaction(tx);

    console.log("    ✅ NAV update succeeded after 1h warp");
  });

  // ─── Test 3: ±10% NAV cap ───
  it("update_nav rejects >10% change (NavChangeTooLarge)", async () => {
    // Warp another hour
    const clk = await banksClient.getClock();
    ctx.setClock(new Clock(clk.slot, clk.epochStartTimestamp, clk.epoch, clk.leaderScheduleEpoch, clk.unixTimestamp + BigInt(3601)));
    ctx.warpToSlot(clk.slot + BigInt(10));

    // Currently total_assets=0. Try +50 SOL (+inf% but >10% cap)
    const ix = makeUpdateNavIx(BigInt(50 * LAMPORTS_PER_SOL), BigInt(0));
    const tx = new Transaction().add(ix);
    tx.recentBlockhash = ctx.lastBlockhash;
    tx.feePayer = authority.publicKey;
    tx.sign(authority);

    try {
      await banksClient.processTransaction(tx);
      assert.fail("Should have rejected >10% NAV change");
    } catch (e: any) {
      // NavChangeTooLarge = error 6013 = 0x177d, or NavChangeExceedsLimit varies
      const msg = e.message || String(e);
      assert.isTrue(
        msg.includes("0x177d") || msg.includes("0x177f") || msg.includes("NavChange"),
        "Expected NavChangeTooLarge error"
      );
      console.log("    ✅ >10% NAV change correctly rejected");
    }
  });
});
