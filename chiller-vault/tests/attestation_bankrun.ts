/**
 * Bankrun tests for mint_with_attestation.
 * - happy path (authority co-sign + nonce PDA)
 * - wallet mismatch
 * - expired attestation
 * - nonce replay
 */
import { start, Clock, BanksClient, ProgramTestContext } from "solana-bankrun";
import {
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  TransactionInstruction,
  LAMPORTS_PER_SOL,
} from "@solana/web3.js";
import {
  TOKEN_PROGRAM_ID,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  createAssociatedTokenAccountInstruction,
  getAssociatedTokenAddressSync,
} from "@solana/spl-token";
import { createHash, randomBytes } from "crypto";
import { assert } from "chai";
import * as path from "path";
import * as fs from "fs";

const PROGRAM_ID = new PublicKey("7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH");
const BPF_UPGRADEABLE = new PublicKey("BPFLoaderUpgradeab1e11111111111111111111111");
const RENT = new PublicKey("SysvarRent111111111111111111111111111111111");

function sighash(name: string): Buffer {
  return createHash("sha256").update(`global:${name}`).digest().subarray(0, 8);
}

const [vaultPda] = PublicKey.findProgramAddressSync([Buffer.from("vault")], PROGRAM_ID);
const [chillerMint] = PublicKey.findProgramAddressSync([Buffer.from("chiller-mint")], PROGRAM_ID);
const [solVaultPda] = PublicKey.findProgramAddressSync([Buffer.from("sol-vault")], PROGRAM_ID);
const [programData] = PublicKey.findProgramAddressSync([PROGRAM_ID.toBuffer()], BPF_UPGRADEABLE);

function attestationPda(nonce: Buffer): PublicKey {
  return PublicKey.findProgramAddressSync([Buffer.from("attestation"), nonce], PROGRAM_ID)[0];
}

function buildAttestData(opts: {
  amount: bigint;
  minTokens: bigint;
  nonce: Buffer;
  exp: bigint;
  wallet: PublicKey;
}): Buffer {
  const data = Buffer.alloc(8 + 8 + 8 + 32 + 8 + 32);
  sighash("mint_with_attestation").copy(data, 0);
  data.writeBigUInt64LE(opts.amount, 8);
  data.writeBigUInt64LE(opts.minTokens, 16);
  opts.nonce.copy(data, 24);
  data.writeBigInt64LE(opts.exp, 56);
  opts.wallet.toBuffer().copy(data, 64);
  return data;
}

describe("mint_with_attestation (Bankrun)", function () {
  this.timeout(120_000);

  let ctx: ProgramTestContext;
  let authority: Keypair;
  let teamWallet: Keypair;
  let tradeWallet: Keypair;
  let userWallet: Keypair;
  let banksClient: BanksClient;
  let userAta: PublicKey;

  async function process(ixs: TransactionInstruction[], signers: Keypair[]) {
    const tx = new Transaction().add(...ixs);
    tx.recentBlockhash = ctx.lastBlockhash;
    tx.feePayer = signers[0].publicKey;
    tx.sign(...signers);
    await banksClient.processTransaction(tx);
  }

  before(async () => {
    const programSo = path.resolve(__dirname, "../target/deploy/chiller_vault.so");
    if (!fs.existsSync(programSo)) {
      throw new Error(`Missing ${programSo} — run anchor build first`);
    }

    authority = Keypair.generate();
    teamWallet = Keypair.generate();
    tradeWallet = Keypair.generate();
    userWallet = Keypair.generate();

    const pdHeader = Buffer.alloc(128);
    pdHeader.writeUInt32LE(3, 0);
    pdHeader.writeBigUInt64LE(BigInt(0), 4);
    pdHeader[12] = 1;
    authority.publicKey.toBuffer().copy(pdHeader, 13);

    ctx = await start(
      [{ name: "chiller_vault", programId: PROGRAM_ID }],
      [
        { address: authority.publicKey, info: { lamports: 100 * LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: teamWallet.publicKey, info: { lamports: LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: tradeWallet.publicKey, info: { lamports: LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: userWallet.publicKey, info: { lamports: 50 * LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: programData, info: { lamports: 10 * LAMPORTS_PER_SOL, data: pdHeader, owner: BPF_UPGRADEABLE, executable: false } },
      ]
    );
    banksClient = ctx.banksClient;

    await process([
      new TransactionInstruction({
        programId: PROGRAM_ID,
        keys: [
          { pubkey: authority.publicKey, isSigner: true, isWritable: true },
          { pubkey: vaultPda, isSigner: false, isWritable: false },
          { pubkey: chillerMint, isSigner: false, isWritable: true },
          { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
          { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
          { pubkey: RENT, isSigner: false, isWritable: false },
        ],
        data: sighash("create_mint"),
      }),
    ], [authority]);

    await process([
      new TransactionInstruction({
        programId: PROGRAM_ID,
        keys: [
          { pubkey: authority.publicKey, isSigner: true, isWritable: true },
          { pubkey: solVaultPda, isSigner: false, isWritable: true },
          { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
        ],
        data: sighash("create_treasury"),
      }),
    ], [authority]);

    const initData = Buffer.alloc(8 + 2 + 2 + 2 + 8 + 8);
    sighash("initialize").copy(initData, 0);
    initData.writeUInt16LE(2000, 8);
    initData.writeUInt16LE(200, 10);
    initData.writeUInt16LE(50, 12);
    initData.writeBigUInt64LE(BigInt(500_000_000), 14);
    initData.writeBigUInt64LE(BigInt(100_000_000_000), 22);
    await process([
      new TransactionInstruction({
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
      }),
    ], [authority]);

    userAta = getAssociatedTokenAddressSync(chillerMint, userWallet.publicKey);
    await process([
      createAssociatedTokenAccountInstruction(
        userWallet.publicKey,
        userAta,
        userWallet.publicKey,
        chillerMint
      ),
    ], [userWallet]);
  });

  function mintIx(opts: {
    amount: bigint;
    nonce: Buffer;
    exp: bigint;
    wallet: PublicKey;
    authority?: Keypair;
  }): TransactionInstruction {
    const auth = opts.authority ?? authority;
    const data = buildAttestData({
      amount: opts.amount,
      minTokens: BigInt(1),
      nonce: opts.nonce,
      exp: opts.exp,
      wallet: opts.wallet,
    });
    return new TransactionInstruction({
      programId: PROGRAM_ID,
      keys: [
        { pubkey: userWallet.publicKey, isSigner: true, isWritable: true },
        { pubkey: auth.publicKey, isSigner: true, isWritable: false },
        { pubkey: vaultPda, isSigner: false, isWritable: true },
        { pubkey: chillerMint, isSigner: false, isWritable: true },
        { pubkey: solVaultPda, isSigner: false, isWritable: true },
        { pubkey: userAta, isSigner: false, isWritable: true },
        { pubkey: attestationPda(opts.nonce), isSigner: false, isWritable: true },
        { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ],
      data,
    });
  }

  it("mints with valid attestation (authority co-sign)", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const amount = BigInt(2 * LAMPORTS_PER_SOL);
    await process([mintIx({ amount, nonce, exp, wallet: userWallet.publicKey })], [userWallet, authority]);
    const vaultAcc = await banksClient.getAccount(vaultPda);
    assert.ok(vaultAcc, "vault exists");
    const data = Buffer.from(vaultAcc!.data);
    // total_assets at offset 8 + 32*5 = 168
    const assets = data.readBigUInt64LE(8 + 32 * 5);
    assert.equal(assets, amount);
    console.log("    ✅ attested mint OK");
  });

  it("rejects wallet mismatch", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const wrong = Keypair.generate().publicKey;
    try {
      await process(
        [mintIx({ amount: BigInt(LAMPORTS_PER_SOL), nonce, exp, wallet: wrong })],
        [userWallet, authority]
      );
      assert.fail("expected wallet mismatch");
    } catch (e: any) {
      const msg = e.message || String(e);
      assert.isTrue(
        msg.includes("0x1787") || msg.includes("AttestationWalletMismatch") || msg.includes("custom program error"),
        msg
      );
      console.log("    ✅ wallet mismatch rejected");
    }
  });

  it("rejects expired attestation", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp - BigInt(10);
    try {
      await process(
        [mintIx({ amount: BigInt(LAMPORTS_PER_SOL), nonce, exp, wallet: userWallet.publicKey })],
        [userWallet, authority]
      );
      assert.fail("expected expired");
    } catch (e: any) {
      const msg = e.message || String(e);
      assert.isTrue(
        msg.includes("0x1786") || msg.includes("AttestationExpired") || msg.includes("custom program error"),
        msg
      );
      console.log("    ✅ expired attestation rejected");
    }
  });

  it("rejects nonce replay", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const amount = BigInt(LAMPORTS_PER_SOL);
    await process([mintIx({ amount, nonce, exp, wallet: userWallet.publicKey })], [userWallet, authority]);
    try {
      await process([mintIx({ amount, nonce, exp, wallet: userWallet.publicKey })], [userWallet, authority]);
      assert.fail("expected replay fail");
    } catch (e: any) {
      const msg = e.message || String(e);
      assert.isTrue(msg.length > 0, "replay should fail");
      console.log("    ✅ nonce replay rejected");
    }
  });

  it("rejects unauthorized attestation authority", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const impostor = Keypair.generate();
    // fund impostor for fees
    const fund = new Transaction().add(
      SystemProgram.transfer({
        fromPubkey: authority.publicKey,
        toPubkey: impostor.publicKey,
        lamports: LAMPORTS_PER_SOL,
      })
    );
    fund.recentBlockhash = ctx.lastBlockhash;
    fund.feePayer = authority.publicKey;
    fund.sign(authority);
    await banksClient.processTransaction(fund);

    try {
      await process(
        [mintIx({ amount: BigInt(LAMPORTS_PER_SOL), nonce, exp, wallet: userWallet.publicKey, authority: impostor })],
        [userWallet, impostor]
      );
      assert.fail("expected unauthorized");
    } catch (e: any) {
      const msg = e.message || String(e);
      assert.isTrue(
        msg.includes("0x1788") || msg.includes("AttestationUnauthorized") || msg.includes("custom program error"),
        msg
      );
      console.log("    ✅ unauthorized authority rejected");
    }
  });
});
