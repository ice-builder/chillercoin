import { Connection, PublicKey } from "@solana/web3.js";

const PROGRAM_ID = new PublicKey("7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH");
const BPF_UPGRADEABLE = new PublicKey("BPFLoaderUpgradeab1e11111111111111111111111");
const conn = new Connection("http://127.0.0.1:8899", "confirmed");

async function main() {
  const [programData] = PublicKey.findProgramAddressSync([PROGRAM_ID.toBuffer()], BPF_UPGRADEABLE);
  console.log("ProgramData PDA:", programData.toBase58());

  const info = await conn.getAccountInfo(programData);
  if (!info) {
    console.log("ERROR: ProgramData account not found!");
    return;
  }
  
  console.log("Data length:", info.data.length);
  console.log("Owner:", info.owner.toBase58());
  
  // Dump first 80 bytes hex
  const hex = Buffer.from(info.data).subarray(0, 80).toString("hex");
  console.log("First 80 bytes hex:", hex);
  
  // Try to read upgrade authority at different offsets
  // Standard layout: 4(type) + 8(slot) + 1(option) + 32(pubkey) = offset 13..45
  const optionTag = info.data[12];
  console.log("Byte 12 (Option tag):", optionTag);
  
  if (optionTag === 1) {
    const auth = new PublicKey(info.data.subarray(13, 45));
    console.log("Upgrade authority (13..45):", auth.toBase58());
  } else {
    console.log("Option tag is not 1, authority may be at different offset");
    // Try offset 45..77 (if there's extra padding)
    for (const off of [4, 8, 12, 13, 44, 45]) {
      try {
        const tag = info.data[off];
        if (tag === 1 && off + 33 <= info.data.length) {
          const auth = new PublicKey(info.data.subarray(off + 1, off + 33));
          console.log(`Found authority at offset ${off + 1}..${off + 33}: ${auth.toBase58()}`);
        }
      } catch(e) {}
    }
  }
  
  // Show what authority the test uses
  const authorityKp = JSON.parse(require("fs").readFileSync(
    require("os").homedir() + "/.config/solana/id.json", "utf-8"
  ));
  const { Keypair } = await import("@solana/web3.js");
  const kp = Keypair.fromSecretKey(Uint8Array.from(authorityKp));
  console.log("Test authority:", kp.publicKey.toBase58());
}

main().catch(console.error);
