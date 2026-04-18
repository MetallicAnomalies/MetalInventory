// push-to-r2.ts
import { execSync } from "child_process";
import { readdirSync } from "fs";

const BUCKET = "scrobex-metadata";
const LOCAL_DIR = "./output/shards";
const R2_PREFIX = "shards";

for (const file of readdirSync(LOCAL_DIR)) {
    const key = `${R2_PREFIX}/${file}`;
    console.log(`Uploading ${key}...`);
    try {
        execSync(`wrangler r2 object put ${BUCKET}/${key} --file=${LOCAL_DIR}/${file} --remote --content-type=application/json`, {
            stdio: "inherit"
        });
    } catch (err) {
        console.error(`Failed to upload ${key}:`, err);
    }
}