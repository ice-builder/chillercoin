import * as anchor from "@coral-xyz/anchor";
import { Connection, Keypair, PublicKey, SystemProgram, Transaction, TransactionInstruction, LAMPORTS_PER_SOL, sendAndConfirmTransaction } from "@solana/web3.js";
import { TOKEN_PROGRAM_ID, createAssociatedTokenAccountInstruction, getAssociatedTokenAddress, getAccount } from "@solana/spl-token";
import { createHash } from "crypto";
import { assert } from "chai";

// ═══════════════════════════════════════════════
// Config
// ═══════════════════════════════════════════════
const PROGRAM_ID = new PublicKey("7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH");
const conn = new Connection("http://127.0.0.1:8899", "confirmed");

function sighash(ns: string, name: string): Buffer {
  const preimage = `global:${name}`;
  return createHash("sha256").update(preimage).digest().subarray(0, 8);
}

// ═══════════════════════════════════════════════
// PDAs
// ═══════════════════════════════════════════════
const [vaultPda, vaultBump] = PublicKey.findProgramAddressSync([Buffer.from("vault")], PROGRAM_ID);
const [chillerMint, mintBump] = PublicKey.findProgramAddressSync([Buffer.from("chiller-mint")], PROGRAM_ID);
const [solVaultPda, solVaultBump] = PublicKey.findProgramAddressSync([Buffer.from("sol-vault")], PROGRAM_ID);

// ═══════════════════════════════════════════════
// Wallets
// ═══════════════════════════════════════════════
const authorityKp = Keypair.fromSecretKey(
  Uint8Array.from(JSON.parse(require("fs").readFileSync(
    require("os").homedir() + "/.config/solana/id.json", "utf-8"
  )))
);
const teamWallet = Keypair.generate();
const userWallet = Keypair.generate();

async function airdrop(pubkey: PublicKey, sol: number) {
  const sig = await conn.requestAirdrop(pubkey, sol * LAMPORTS_PER_SOL);
  await conn.confirmTransaction(sig, "confirmed");
}

function decodeBool(buf: Buffer, off: number): boolean { return buf[off] !== 0; }
function decodeU64(buf: Buffer, off: number): bigint { return buf.readBigUInt64LE(off); }
function decodeI64(buf: Buffer, off: number): bigint { return buf.readBigInt64LE(off); }
function decodeU16(buf: Buffer, off: number): number { return buf.readUInt16LE(off); }
function decodePubkey(buf: Buffer, off: number): PublicKey { return new PublicKey(buf.subarray(off, off + 32)); }

interface VaultState {
  authority: PublicKey; chillerMint: PublicKey; teamWallet: PublicKey;
  totalAssets: bigint; totalSupply: bigint; highWaterMark: bigint;
  totalTrades: bigint; totalWins: bigint; cumulativePnlBps: bigint;
  perfFeeBps: number; mgmtFeeBps: number; wdFeeBps: number;
  minDeposit: bigint; maxWdPerEpoch: bigint; epochWithdrawals: bigint;
  currentEpoch: bigint; lastNavUpdate: bigint; isPaused: boolean;
  bump: number; chillerMintBump: number; solVaultBump: number;
}

function decodeVaultState(data: Buffer): VaultState {
  let o = 8; // skip discriminator
  return {
    authority: decodePubkey(data, o), chillerMint: decodePubkey(data, o += 32),
    teamWallet: decodePubkey(data, o += 32), totalAssets: decodeU64(data, o += 32),
    totalSupply: decodeU64(data, o += 8), highWaterMark: decodeU64(data, o += 8),
    totalTrades: decodeU64(data, o += 8), totalWins: decodeU64(data, o += 8),
    cumulativePnlBps: decodeI64(data, o += 8), perfFeeBps: decodeU16(data, o += 8),
    mgmtFeeBps: decodeU16(data, o += 2), wdFeeBps: decodeU16(data, o += 2),
    minDeposit: decodeU64(data, o += 2), maxWdPerEpoch: decodeU64(data, o += 8),
    epochWithdrawals: decodeU64(data, o += 8), currentEpoch: decodeU64(data, o += 8),
    lastNavUpdate: decodeI64(data, o += 8), isPaused: decodeBool(data, o += 8),
    bump: data[o += 1], chillerMintBump: data[o += 1], solVaultBump: data[o += 1],
  };
}

async function getVault(): Promise<VaultState> {
  const info = await conn.getAccountInfo(vaultPda);
  return decodeVaultState(info!.data as Buffer);
}

// ═══════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════
describe("$CHILLER SOL Vault", () => {
  let userChillerAta: PublicKey;

  before(async () => {
    await airdrop(authorityKp.publicKey, 100);
    await airdrop(teamWallet.publicKey, 1);
    await airdrop(userWallet.publicKey, 50);
  });

  it("1. create_mint", async () => {
    const ix = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authorityKp.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: false },
        { pubkey: chillerMint, isSigner: false, isWritable: true },
        { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
        { pubkey: anchor.web3.SYSVAR_RENT_PUBKEY, isSigner: false, isWritable: false },
      ],
      data: sighash("global", "create_mint"),
    });
    const tx = new Transaction().add(ix);
    await sendAndConfirmTransaction(conn, tx, [authorityKp]);
    const mint = await conn.getAccountInfo(chillerMint);
    assert.ok(mint, "Mint created");
  });

  it("2. create_treasury", async () => {
    const ix = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authorityKp.publicKey, isSigner: true, isWritable: true },
        { pubkey: solVaultPda, isSigner: false, isWritable: true },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ],
      data: sighash("global", "create_treasury"),
    });
    const tx = new Transaction().add(ix);
    await sendAndConfirmTransaction(conn, tx, [authorityKp]);
    const info = await conn.getAccountInfo(solVaultPda);
    assert.ok(info, "Treasury created");
    assert.equal(info!.owner.toString(), PROGRAM_ID.toString(), "Owned by program");
  });

  it("3. initialize", async () => {
    const data = Buffer.alloc(8 + 2 + 2 + 2 + 8 + 8);
    sighash("global", "initialize").copy(data, 0);
    data.writeUInt16LE(2000, 8);   // perf_fee = 20%
    data.writeUInt16LE(200, 10);   // mgmt_fee = 2%
    data.writeUInt16LE(50, 12);    // wd_fee = 0.5%
    data.writeBigUInt64LE(BigInt(500_000_000), 14);  // min_deposit = 0.5 SOL
    data.writeBigUInt64LE(BigInt(100_000_000_000), 22); // max_wd = 100 SOL/epoch

    const ix = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authorityKp.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
        { pubkey: chillerMint, isSigner: false, isWritable: false },
        { pubkey: solVaultPda, isSigner: false, isWritable: true },
        { pubkey: teamWallet.publicKey, isSigner: false, isWritable: false },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ],
      data,
    });
    const tx = new Transaction().add(ix);
    await sendAndConfirmTransaction(conn, tx, [authorityKp]);
    const v = await getVault();
    assert.equal(v.perfFeeBps, 2000);
    assert.equal(v.minDeposit, BigInt(500_000_000));
    assert.equal(v.isPaused, false);
  });

  it("3. deposit 5 SOL → receive $CHILLER", async () => {
    // Create user's $CHILLER ATA
    userChillerAta = await getAssociatedTokenAddress(chillerMint, userWallet.publicKey);
    const createAtaIx = createAssociatedTokenAccountInstruction(
      userWallet.publicKey, userChillerAta, userWallet.publicKey, chillerMint
    );
    const ataTx = new Transaction().add(createAtaIx);
    await sendAndConfirmTransaction(conn, ataTx, [userWallet]);

    // Deposit 5 SOL
    const amount = BigInt(5 * LAMPORTS_PER_SOL);
    const data = Buffer.alloc(8 + 8);
    sighash("global", "deposit").copy(data, 0);
    data.writeBigUInt64LE(amount, 8);

    const ix = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: userWallet.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
        { pubkey: chillerMint, isSigner: false, isWritable: true },
        { pubkey: solVaultPda, isSigner: false, isWritable: true },
        { pubkey: userChillerAta, isSigner: false, isWritable: true },
        { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ],
      data,
    });
    const tx = new Transaction().add(ix);
    await sendAndConfirmTransaction(conn, tx, [userWallet]);

    const v = await getVault();
    assert.equal(v.totalAssets, amount);
    assert.ok(v.totalSupply > 0n, "Supply minted");

    const ata = await getAccount(conn, userChillerAta);
    assert.ok(ata.amount > 0n, "User got $CHILLER");
    console.log(`    Deposited: 5 SOL → ${ata.amount} $CHILLER`);
  });

  it("4. update_nav (simulate profit)", async () => {
    // Simulate: total assets grew from 5 SOL to 5.8 SOL
    const newTotal = BigInt(5_800_000_000); // 5.8 SOL
    const data = Buffer.alloc(8 + 8);
    sighash("global", "update_nav").copy(data, 0);
    data.writeBigUInt64LE(newTotal, 8);

    const ix = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authorityKp.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
        { pubkey: solVaultPda, isSigner: false, isWritable: true },
        { pubkey: teamWallet.publicKey, isSigner: false, isWritable: true },
      ],
      data,
    });
    const tx = new Transaction().add(ix);
    await sendAndConfirmTransaction(conn, tx, [authorityKp]);

    const v = await getVault();
    console.log(`    NAV updated: total_assets = ${v.totalAssets} lamports`);
    assert.ok(v.totalAssets > 0n);
  });

  it("5. log_trade (BTC LONG +2.1%)", async () => {
    const pair = "BTCUSDT";
    const side = "LONG";
    const buf = Buffer.alloc(8 + 4 + pair.length + 4 + side.length + 8 + 8 + 4 + 8 + 8);
    let off = 0;
    sighash("global", "log_trade").copy(buf, off); off += 8;
    buf.writeUInt32LE(pair.length, off); off += 4;
    buf.write(pair, off); off += pair.length;
    buf.writeUInt32LE(side.length, off); off += 4;
    buf.write(side, off); off += side.length;
    buf.writeBigUInt64LE(BigInt(61500_000000), off); off += 8;
    buf.writeBigUInt64LE(BigInt(62791_000000), off); off += 8;
    buf.writeInt32LE(210, off); off += 4;
    buf.writeBigInt64LE(BigInt(645_000000), off); off += 8;
    buf.writeBigUInt64LE(BigInt(3600), off); off += 8;

    const ix = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authorityKp.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
      ],
      data: buf.subarray(0, off),
    });
    const tx = new Transaction().add(ix);
    await sendAndConfirmTransaction(conn, tx, [authorityKp]);
    const v = await getVault();
    assert.equal(v.totalTrades, 1n);
    assert.equal(v.totalWins, 1n);
    console.log(`    Trade logged: ${pair} ${side} +2.10%`);
  });

  it("6. drain_to_trade (authority withdraws SOL for Drift)", async () => {
    const drainAmount = BigInt(2 * LAMPORTS_PER_SOL); // 2 SOL
    const beforeBal = await conn.getBalance(solVaultPda);

    const data = Buffer.alloc(8 + 8);
    sighash("global", "drain_to_trade").copy(data, 0);
    data.writeBigUInt64LE(drainAmount, 8);

    const ix = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authorityKp.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: false },
        { pubkey: solVaultPda, isSigner: false, isWritable: true },
      ],
      data,
    });
    const tx = new Transaction().add(ix);
    await sendAndConfirmTransaction(conn, tx, [authorityKp]);

    const afterBal = await conn.getBalance(solVaultPda);
    console.log(`    Drained: ${beforeBal} → ${afterBal} lamports (-${drainAmount} for Drift)`);
    assert.ok(afterBal < beforeBal);
  });

  it("7. fund_vault (return profits from Drift)", async () => {
    const fundAmount = BigInt(3 * LAMPORTS_PER_SOL); // 3 SOL (profit!)
    const beforeBal = await conn.getBalance(solVaultPda);

    const data = Buffer.alloc(8 + 8);
    sighash("global", "fund_vault").copy(data, 0);
    data.writeBigUInt64LE(fundAmount, 8);

    const ix = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authorityKp.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: false },
        { pubkey: solVaultPda, isSigner: false, isWritable: true },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ],
      data,
    });
    const tx = new Transaction().add(ix);
    await sendAndConfirmTransaction(conn, tx, [authorityKp]);

    const afterBal = await conn.getBalance(solVaultPda);
    console.log(`    Funded: ${beforeBal} → ${afterBal} lamports (+${fundAmount} from Drift)`);
    assert.ok(afterBal > beforeBal);
  });

  it("8. withdraw $CHILLER → receive SOL", async () => {
    const ata = await getAccount(conn, userChillerAta);
    const burnTokens = ata.amount / 2n; // Withdraw half
    const userBalBefore = await conn.getBalance(userWallet.publicKey);

    const data = Buffer.alloc(8 + 8);
    sighash("global", "withdraw").copy(data, 0);
    data.writeBigUInt64LE(burnTokens, 8);

    const ix = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: userWallet.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
        { pubkey: chillerMint, isSigner: false, isWritable: true },
        { pubkey: solVaultPda, isSigner: false, isWritable: true },
        { pubkey: userChillerAta, isSigner: false, isWritable: true },
        { pubkey: teamWallet.publicKey, isSigner: false, isWritable: true },
        { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
      ],
      data,
    });
    const tx = new Transaction().add(ix);
    await sendAndConfirmTransaction(conn, tx, [userWallet]);

    const userBalAfter = await conn.getBalance(userWallet.publicKey);
    const received = userBalAfter - userBalBefore;
    console.log(`    Withdrew: ${burnTokens} $CHILLER → +${received} lamports (${(Number(received) / LAMPORTS_PER_SOL).toFixed(4)} SOL)`);
    assert.ok(received > 0, "User received SOL");
  });

  it("9. pause → unpause", async () => {
    // Pause
    const pauseData = Buffer.alloc(8 + 1);
    sighash("global", "set_paused").copy(pauseData, 0);
    pauseData[8] = 1; // true

    const pauseIx = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authorityKp.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
      ],
      data: pauseData,
    });
    await sendAndConfirmTransaction(conn, new Transaction().add(pauseIx), [authorityKp]);
    let v = await getVault();
    assert.equal(v.isPaused, true);

    // Unpause
    const unpauseData = Buffer.alloc(8 + 1);
    sighash("global", "set_paused").copy(unpauseData, 0);
    unpauseData[8] = 0;

    const unpauseIx = new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: authorityKp.publicKey, isSigner: true, isWritable: true },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
      ],
      data: unpauseData,
    });
    await sendAndConfirmTransaction(conn, new Transaction().add(unpauseIx), [authorityKp]);
    v = await getVault();
    assert.equal(v.isPaused, false);
  });

  it("10. final status", async () => {
    const v = await getVault();
    const solBal = await conn.getBalance(solVaultPda);
    console.log(`\n    ═══════════════════════════════════════`);
    console.log(`    🧊 $CHILLER SOL Vault — Final State`);
    console.log(`    ═══════════════════════════════════════`);
    console.log(`    Total Assets:  ${Number(v.totalAssets) / LAMPORTS_PER_SOL} SOL`);
    console.log(`    Total Supply:  ${v.totalSupply} $CHILLER`);
    console.log(`    Sol Vault Bal: ${solBal / LAMPORTS_PER_SOL} SOL`);
    console.log(`    Trades:        ${v.totalTrades} (${v.totalWins} wins)`);
    console.log(`    Paused:        ${v.isPaused}`);
    console.log(`    ═══════════════════════════════════════\n`);
  });
});
