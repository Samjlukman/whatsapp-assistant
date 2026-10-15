// WhatsApp <-> FastAPI bridge. Runs on the spare (bot) number via LocalAuth.
const fs = require('fs');
const http = require('http');
const path = require('path');
const qrcode = require('qrcode-terminal');
const { Client, LocalAuth } = require('whatsapp-web.js');

// SECURITY: the only number allowed to command the bot and receive messages. Digits only, with country code.
// Kept in owner_number.txt (git-ignored) so the repo never contains it.
const OWNER_FILE = path.join(__dirname, 'owner_number.txt');
const OWNER_NUMBER = fs.existsSync(OWNER_FILE) ? fs.readFileSync(OWNER_FILE, 'utf8').trim() : '';
if (!/^\d{8,15}$/.test(OWNER_NUMBER)) {
  console.error(`Put your number (digits only, with country code, e.g. 447...) in ${OWNER_FILE}`);
  process.exit(1);
}
const OWNER_CUS = `${OWNER_NUMBER}@c.us`;
const BACKEND = 'http://127.0.0.1:8000/incoming';
const PORT = 3001;
const SECRET = process.env.BRIDGE_SECRET;
if (!SECRET) { console.error('Set the BRIDGE_SECRET environment variable first.'); process.exit(1); }

const client = new Client({ authStrategy: new LocalAuth() });
let ready = false;
client.on('qr', (qr) => qrcode.generate(qr, { small: true }));
client.on('ready', () => { ready = true; console.log('WhatsApp ready'); });
client.on('disconnected', (reason) => { ready = false; console.error('WhatsApp disconnected:', reason); });

async function isOwner(msg) {
  if (msg.fromMe) return false;
  if (msg.from === OWNER_CUS) return true;
  // Newer WhatsApp accounts arrive as <id>@lid instead of <number>@c.us; resolve to the real number.
  if (!msg.from.endsWith('@lid')) return false; // groups, broadcasts, everyone else
  // contact.number holds the lid digits for these senders, not the phone number, so ask WhatsApp for the mapping.
  const [{ pn }] = await client.getContactLidAndPhone([msg.from]);
  return pn?.split('@')[0] === OWNER_NUMBER;
}

client.on('message', async (msg) => {
  let owner = false;
  try {
    owner = await isOwner(msg);
  } catch (e) {
    console.error('Owner check failed:', e);
  }
  if (!owner) { console.log('Ignored message from', msg.from); return; } // no reply to strangers
  if (msg.type !== 'chat') return; // text only
  let reply;
  try {
    const res = await fetch(BACKEND, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-bridge-secret': SECRET },
      body: JSON.stringify({ text: msg.body }),
    });
    reply = res.ok ? (await res.json()).reply : `Backend error ${res.status}.`;
  } catch {
    reply = 'The assistant backend is offline (is main.py running?).';
  }
  await client.sendMessage(msg.from, reply); // msg.from is verified as the owner above
});

async function sendToOwner(text) {
  const id = await client.getNumberId(OWNER_NUMBER);
  await client.sendMessage(id ? id._serialized : OWNER_CUS, text);
}

// Local endpoint for reminders from the Python scheduler. Always sends to the owner, never anyone else.
http.createServer((req, res) => {
  if (req.method !== 'POST' || req.url !== '/send' || req.headers['x-bridge-secret'] !== SECRET) {
    res.writeHead(403).end();
    return;
  }
  let body = '';
  req.on('data', (chunk) => { body += chunk; });
  req.on('end', async () => {
    try {
      if (!ready) throw new Error('WhatsApp not connected yet');
      await sendToOwner(String(JSON.parse(body).text));
      res.writeHead(200).end('ok');
    } catch (e) {
      res.writeHead(503).end(String(e)); // the scheduler retries
    }
  });
}).listen(PORT, '127.0.0.1', () => console.log(`Bridge listening on 127.0.0.1:${PORT}`));

client.initialize();
