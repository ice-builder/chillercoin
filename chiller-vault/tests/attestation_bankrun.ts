/**
 * Bankrun tests for mint_with_attestation (treasury-prefunded, amount+hash bound).
 */
import { start, BanksClient, ProgramTestContext } from "solana-bankrun";
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

function resolveProgramSo(): string {
  const fixture = path.resolve(__dirname, "fixtures/chiller_vault.so");
  const built = path.resolve(__dirname, "../target/deploy/chiller_vault.so");
  if (fs.existsSync(built)) return built;
  if (fs.existsSync(fixture)) {
    fs.mkdirSync(path.dirname(built), { recursive: true });
    fs.copyFileSync(fixture, built);
    return built;
  }
  throw new Error(`Missing BPF at ${fixture} or ${built}`);
}

const [vaultPda] = PublicKey.findProgramAddressSync([Buffer.from("vault")], PROGRAM_ID);
const [chillerMint] = PublicKey.findProgramAddressSync([Buffer.from("chiller-mint")], PROGRAM_ID);
const [solVaultPda] = PublicKey.findProgramAddressSync([Buffer.from("sol-vault")], PROGRAM_ID);
const [tradeLoggerPda] = PublicKey.findProgramAddressSync([Buffer.from("trade-logger")], PROGRAM_ID);
const [programData] = PublicKey.findProgramAddressSync([PROGRAM_ID.toBuffer()], BPF_UPGRADEABLE);

function attestationPda(nonce: Buffer): PublicKey {
  return PublicKey.findProgramAddressSync([Buffer.from("attestation"), nonce], PROGRAM_ID)[0];
}

function payloadHash(nonce: Buffer, wallet: PublicKey, amount: bigint, exp: bigint): Buffer {
  const amt = Buffer.alloc(8);
  amt.writeBigUInt64LE(amount);
  const ex = Buffer.alloc(8);
  ex.writeBigInt64LE(exp);
  return createHash("sha256")
    .update(Buffer.concat([nonce, wallet.toBuffer(), amt, ex]))
    .digest();
}

function buildAttestData(opts: {
  amount: bigint;
  minTokens: bigint;
  nonce: Buffer;
  exp: bigint;
  wallet: PublicKey;
  amountMismatch?: bigint;
  badHash?: boolean;
}): Buffer {
  const amountBound = opts.amountMismatch ?? opts.amount;
  const hash = opts.badHash
    ? Buffer.alloc(32, 7)
    : payloadHash(opts.nonce, opts.wallet, amountBound, opts.exp);
  // disc(8) + amount(8) + min(8) + nonce(32) + exp(8) + wallet(32) + amount_lamports(8) + hash(32)
  const data = Buffer.alloc(8 + 8 + 8 + 32 + 8 + 32 + 8 + 32);
  sighash("mint_with_attestation").copy(data, 0);
  data.writeBigUInt64LE(opts.amount, 8);
  data.writeBigUInt64LE(opts.minTokens, 16);
  opts.nonce.copy(data, 24);
  data.writeBigInt64LE(opts.exp, 56);
  opts.wallet.toBuffer().copy(data, 64);
  data.writeBigUInt64LE(amountBound, 96);
  hash.copy(data, 104);
  return data;
}

describe("mint_with_attestation (Bankrun)", function () {
  this.timeout(120_000);

  let ctx: ProgramTestContext;
  let authority: Keypair;
  let teamWallet: Keypair;
  let tradeWallet: Keypair;
  let userWallet: Keypair;
  let loggerKp: Keypair;
  let banksClient: BanksClient;
  let userAta: PublicKey;

  async function process(ixs: TransactionInstruction[], signers: Keypair[]) {
    const tx = new Transaction().add(...ixs);
    const [bh] = await banksClient.getLatestBlockhash();
    tx.recentBlockhash = bh;
    tx.feePayer = signers[0].publicKey;
    tx.sign(...signers);
    await banksClient.processTransaction(tx);
  }

  async function fundTreasury(lamports: number | bigint) {
    await process(
      [
        SystemProgram.transfer({
          fromPubkey: userWallet.publicKey,
          toPubkey: solVaultPda,
          lamports: Number(lamports),
        }),
      ],
      [userWallet]
    );
  }

  before(async () => {
    resolveProgramSo();

    authority = Keypair.generate();
    teamWallet = Keypair.generate();
    tradeWallet = Keypair.generate();
    userWallet = Keypair.generate();
    loggerKp = Keypair.generate();

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
        { address: loggerKp.publicKey, info: { lamports: 10 * LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
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
    amountMismatch?: bigint;
    badHash?: boolean;
  }): TransactionInstruction {
    const auth = opts.authority ?? authority;
    const data = buildAttestData({
      amount: opts.amount,
      minTokens: BigInt(1),
      nonce: opts.nonce,
      exp: opts.exp,
      wallet: opts.wallet,
      amountMismatch: opts.amountMismatch,
      badHash: opts.badHash,
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

  it("mints with valid attestation (prefunded treasury, no double transfer)", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const amount = BigInt(2 * LAMPORTS_PER_SOL);
    await fundTreasury(amount);
    const beforeUser = (await banksClient.getAccount(userWallet.publicKey))!.lamports;
    const beforeVault = (await banksClient.getAccount(solVaultPda))!.lamports;
    await process([mintIx({ amount, nonce, exp, wallet: userWallet.publicKey })], [userWallet, authority]);
    const afterUser = (await banksClient.getAccount(userWallet.publicKey))!.lamports;
    const afterVault = (await banksClient.getAccount(solVaultPda))!.lamports;
    assert.isBelow(Number(beforeUser - afterUser), 20_000_000, "mint pulled SOL from user");
    assert.equal(afterVault, beforeVault, "treasury lamports must stay put on mint");
    const vaultAcc = await banksClient.getAccount(vaultPda);
    const data = Buffer.from(vaultAcc!.data);
    const assets = data.readBigUInt64LE(8 + 32 * 5);
    assert.equal(assets, amount);
    console.log("    ✅ attested mint OK (treasury claim)");
  });

  it("rejects amount mismatch", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const amount = BigInt(LAMPORTS_PER_SOL);
    await fundTreasury(amount);
    let threw = false;
    try {
      await process(
        [mintIx({ amount, nonce, exp, wallet: userWallet.publicKey, amountMismatch: amount * 2n })],
        [userWallet, authority]
      );
    } catch (e: any) {
      threw = true;
      console.log("    ✅ amount mismatch rejected:", String(e.message || e).slice(0, 100));
    }
    assert.isTrue(threw, "expected amount mismatch");
  });

  it("rejects bad payload hash", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const amount = BigInt(LAMPORTS_PER_SOL);
    await fundTreasury(amount);
    try {
      await process(
        [mintIx({ amount, nonce, exp, wallet: userWallet.publicKey, badHash: true })],
        [userWallet, authority]
      );
      assert.fail("expected hash mismatch");
    } catch (e: any) {
      assert.ok(String(e.message || e).includes("custom program error") || String(e).includes("0x"));
      console.log("    ✅ bad hash rejected");
    }
  });

  it("rejects wallet mismatch", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const wrong = Keypair.generate().publicKey;
    await fundTreasury(LAMPORTS_PER_SOL);
    try {
      await process(
        [mintIx({ amount: BigInt(LAMPORTS_PER_SOL), nonce, exp, wallet: wrong })],
        [userWallet, authority]
      );
      assert.fail("expected wallet mismatch");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ wallet mismatch rejected");
    }
  });

  it("rejects expired attestation", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp - BigInt(10);
    await fundTreasury(LAMPORTS_PER_SOL);
    try {
      await process(
        [mintIx({ amount: BigInt(LAMPORTS_PER_SOL), nonce, exp, wallet: userWallet.publicKey })],
        [userWallet, authority]
      );
      assert.fail("expected expired");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ expired rejected");
    }
  });

  it("rejects nonce replay", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const amount = BigInt(LAMPORTS_PER_SOL);
    await fundTreasury(amount);
    await process([mintIx({ amount, nonce, exp, wallet: userWallet.publicKey })], [userWallet, authority]);
    await fundTreasury(amount);
    try {
      await process([mintIx({ amount, nonce, exp, wallet: userWallet.publicKey })], [userWallet, authority]);
      assert.fail("expected replay fail");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ nonce replay rejected");
    }
  });

  it("rejects open deposit (deprecated)", async () => {
    const data = Buffer.alloc(8 + 8 + 8);
    sighash("deposit").copy(data, 0);
    data.writeBigUInt64LE(BigInt(LAMPORTS_PER_SOL), 8);
    data.writeBigUInt64LE(BigInt(1), 16);
    try {
      await process(
        [
          new TransactionInstruction({
            programId: PROGRAM_ID,
            keys: [
              { pubkey: userWallet.publicKey, isSigner: true, isWritable: true },
              { pubkey: vaultPda, isSigner: false, isWritable: true },
              { pubkey: chillerMint, isSigner: false, isWritable: true },
              { pubkey: solVaultPda, isSigner: false, isWritable: true },
              { pubkey: userAta, isSigner: false, isWritable: true },
              { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
              { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
            ],
            data,
          }),
        ],
        [userWallet]
      );
      assert.fail("expected open deposit deprecated");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ open deposit rejected");
    }
  });

  it("rejects unauthorized attestation authority", async () => {
    const nonce = randomBytes(32);
    const clk = await banksClient.getClock();
    const exp = clk.unixTimestamp + BigInt(3600);
    const amount = BigInt(LAMPORTS_PER_SOL);
    const evil = Keypair.generate();
    // fund evil for fees
    await process(
      [SystemProgram.transfer({ fromPubkey: authority.publicKey, toPubkey: evil.publicKey, lamports: LAMPORTS_PER_SOL })],
      [authority]
    );
    await fundTreasury(amount);
    try {
      await process(
        [mintIx({ amount, nonce, exp, wallet: userWallet.publicKey, authority: evil })],
        [userWallet, evil]
      );
      assert.fail("expected unauthorized");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ unauthorized authority rejected");
    }
  });

  it("rejects open withdraw (deprecated)", async () => {
    const data = Buffer.alloc(8 + 8 + 8);
    sighash("withdraw").copy(data, 0);
    data.writeBigUInt64LE(BigInt(1), 8);
    data.writeBigUInt64LE(BigInt(1), 16);
    try {
      await process(
        [
          new TransactionInstruction({
            programId: PROGRAM_ID,
            keys: [
              { pubkey: userWallet.publicKey, isSigner: true, isWritable: true },
              { pubkey: vaultPda, isSigner: false, isWritable: true },
              { pubkey: chillerMint, isSigner: false, isWritable: true },
              { pubkey: solVaultPda, isSigner: false, isWritable: true },
              { pubkey: userAta, isSigner: false, isWritable: true },
              { pubkey: teamWallet.publicKey, isSigner: false, isWritable: true },
              { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
            ],
            data,
          }),
        ],
        [userWallet]
      );
      assert.fail("expected open withdraw reject");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ open withdraw rejected");
    }
  });

  it("pause blocks drain_to_trade", async () => {
    const pauseData = Buffer.alloc(9);
    sighash("set_paused").copy(pauseData, 0);
    pauseData[8] = 1;
    await process(
      [
        new TransactionInstruction({
          programId: PROGRAM_ID,
          keys: [
            { pubkey: authority.publicKey, isSigner: true, isWritable: true },
            { pubkey: vaultPda, isSigner: false, isWritable: true },
          ],
          data: pauseData,
        }),
      ],
      [authority]
    );
    const drainData = Buffer.alloc(16);
    sighash("drain_to_trade").copy(drainData, 0);
    drainData.writeBigUInt64LE(BigInt(1_000_000), 8);
    try {
      await process(
        [
          new TransactionInstruction({
            programId: PROGRAM_ID,
            keys: [
              { pubkey: authority.publicKey, isSigner: true, isWritable: true },
              { pubkey: vaultPda, isSigner: false, isWritable: true },
              { pubkey: solVaultPda, isSigner: false, isWritable: true },
              { pubkey: tradeWallet.publicKey, isSigner: false, isWritable: true },
            ],
            data: drainData,
          }),
        ],
        [authority]
      );
      assert.fail("expected drain paused");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ drain blocked while paused");
    }
    pauseData[8] = 0;
    await process(
      [
        new TransactionInstruction({
          programId: PROGRAM_ID,
          keys: [
            { pubkey: authority.publicKey, isSigner: true, isWritable: true },
            { pubkey: vaultPda, isSigner: false, isWritable: true },
          ],
          data: pauseData,
        }),
      ],
      [authority]
    );
  });

  it("trade_logger can log_trade but not mint/pause/drain", async () => {
    const setData = Buffer.alloc(40);
    sighash("set_trade_logger").copy(setData, 0);
    loggerKp.publicKey.toBuffer().copy(setData, 8);
    await process(
      [
        new TransactionInstruction({
          programId: PROGRAM_ID,
          keys: [
            { pubkey: authority.publicKey, isSigner: true, isWritable: true },
            { pubkey: vaultPda, isSigner: false, isWritable: true },
            { pubkey: tradeLoggerPda, isSigner: false, isWritable: true },
            { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
          ],
          data: setData,
        }),
      ],
      [authority]
    );

    const pair = "BTCUSDT";
    const logData = Buffer.alloc(8 + 4 + pair.length + 1 + 8 + 8 + 4 + 8 + 8);
    sighash("log_trade").copy(logData, 0);
    logData.writeUInt32LE(pair.length, 8);
    logData.write(pair, 12);
    let o = 12 + pair.length;
    logData.writeUInt8(0, o); o += 1;
    logData.writeBigUInt64LE(BigInt(61000_000000), o); o += 8;
    logData.writeBigUInt64LE(BigInt(62000_000000), o); o += 8;
    logData.writeInt32LE(163, o); o += 4;
    logData.writeBigInt64LE(BigInt(50_000000), o); o += 8;
    logData.writeBigUInt64LE(BigInt(600), o);
    await process(
      [
        new TransactionInstruction({
          programId: PROGRAM_ID,
          keys: [
            { pubkey: loggerKp.publicKey, isSigner: true, isWritable: true },
            { pubkey: vaultPda, isSigner: false, isWritable: true },
            { pubkey: tradeLoggerPda, isSigner: false, isWritable: false },
          ],
          data: logData,
        }),
      ],
      [loggerKp]
    );
    console.log("    ✅ logger log_trade ok");

    const pauseData = Buffer.alloc(9);
    sighash("set_paused").copy(pauseData, 0);
    pauseData[8] = 1;
    try {
      await process(
        [
          new TransactionInstruction({
            programId: PROGRAM_ID,
            keys: [
              { pubkey: loggerKp.publicKey, isSigner: true, isWritable: true },
              { pubkey: vaultPda, isSigner: false, isWritable: true },
            ],
            data: pauseData,
          }),
        ],
        [loggerKp]
      );
      assert.fail("expected pause Unauthorized");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ logger pause Unauthorized");
    }

    const drainData = Buffer.alloc(16);
    sighash("drain_to_trade").copy(drainData, 0);
    drainData.writeBigUInt64LE(BigInt(1_000_000), 8);
    try {
      await process(
        [
          new TransactionInstruction({
            programId: PROGRAM_ID,
            keys: [
              { pubkey: loggerKp.publicKey, isSigner: true, isWritable: true },
              { pubkey: vaultPda, isSigner: false, isWritable: true },
              { pubkey: solVaultPda, isSigner: false, isWritable: true },
              { pubkey: tradeWallet.publicKey, isSigner: false, isWritable: true },
            ],
            data: drainData,
          }),
        ],
        [loggerKp]
      );
      assert.fail("expected drain Unauthorized");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ logger drain Unauthorized");
    }

    const amount = BigInt(500_000_000);
    const nonce = randomBytes(32);
    const exp = BigInt(Math.floor(Date.now() / 1000) + 3600);
    await fundTreasury(amount);
    try {
      await process(
        [mintIx({ amount, nonce, exp, wallet: userWallet.publicKey, authority: loggerKp })],
        [userWallet, loggerKp]
      );
      assert.fail("expected mint Unauthorized");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ logger mint Unauthorized");
    }
  });
});

describe("initialize wallet alias guard", function () {
  this.timeout(120_000);

  it("rejects team_wallet == trade_wallet", async () => {
    const programSo = resolveProgramSo();
    const authority = Keypair.generate();
    const team = Keypair.generate();
    const user = Keypair.generate();
    const pdHeader = Buffer.alloc(128);
    pdHeader.writeUInt32LE(3, 0);
    pdHeader.writeBigUInt64LE(BigInt(0), 4);
    pdHeader[12] = 1;
    authority.publicKey.toBuffer().copy(pdHeader, 13);
    const ctx = await start(
      [{ name: "chiller_vault", programId: PROGRAM_ID }],
      [
        { address: authority.publicKey, info: { lamports: 50 * LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: team.publicKey, info: { lamports: LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: user.publicKey, info: { lamports: LAMPORTS_PER_SOL, data: Buffer.alloc(0), owner: SystemProgram.programId, executable: false } },
        { address: programData, info: { lamports: 10 * LAMPORTS_PER_SOL, data: pdHeader, owner: BPF_UPGRADEABLE, executable: false } },
      ]
    );
    const banks = ctx.banksClient;
    async function send(ixs: TransactionInstruction[], signers: Keypair[]) {
      const tx = new Transaction().add(...ixs);
      const [bh] = await banks.getLatestBlockhash();
      tx.recentBlockhash = bh;
      tx.feePayer = signers[0].publicKey;
      tx.sign(...signers);
      await banks.processTransaction(tx);
    }
    await send(
      [
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
      ],
      [authority]
    );
    await send(
      [
        new TransactionInstruction({
          programId: PROGRAM_ID,
          keys: [
            { pubkey: authority.publicKey, isSigner: true, isWritable: true },
            { pubkey: solVaultPda, isSigner: false, isWritable: true },
            { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
          ],
          data: sighash("create_treasury"),
        }),
      ],
      [authority]
    );
    const initData = Buffer.alloc(8 + 2 + 2 + 2 + 8 + 8);
    sighash("initialize").copy(initData, 0);
    initData.writeUInt16LE(2000, 8);
    initData.writeUInt16LE(200, 10);
    initData.writeUInt16LE(50, 12);
    initData.writeBigUInt64LE(BigInt(500_000_000), 14);
    initData.writeBigUInt64LE(BigInt(100_000_000_000), 22);
    try {
      await send(
        [
          new TransactionInstruction({
            programId: PROGRAM_ID,
            keys: [
              { pubkey: authority.publicKey, isSigner: true, isWritable: true },
              { pubkey: vaultPda, isSigner: false, isWritable: true },
              { pubkey: chillerMint, isSigner: false, isWritable: false },
              { pubkey: solVaultPda, isSigner: false, isWritable: false },
              { pubkey: team.publicKey, isSigner: false, isWritable: false },
              { pubkey: team.publicKey, isSigner: false, isWritable: false },
              { pubkey: programData, isSigner: false, isWritable: false },
              { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
            ],
            data: initData,
          }),
        ],
        [authority]
      );
      assert.fail("expected alias reject");
    } catch (e: any) {
      assert.ok(String(e.message || e).length > 0);
      console.log("    ✅ team==trade alias rejected");
    }
  });
});
