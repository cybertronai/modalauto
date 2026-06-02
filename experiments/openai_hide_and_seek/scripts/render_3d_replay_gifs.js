#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { mkdirSync, copyFileSync, readdirSync, statSync } from 'node:fs';
import { createServer } from 'node:net';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const viz = join(root, 'visualization');
const requireFromViz = createRequire(join(viz, 'package.json'));
const { chromium } = requireFromViz('playwright');
const publicDir = join(viz, 'public');
const generated = join(publicDir, 'generated_3d');
const journalArtifacts = join(root, 'journal', 'artifacts');
mkdirSync(generated, { recursive: true });

function artifactDirs() {
  return readdirSync(journalArtifacts)
    .map((name) => join(journalArtifacts, name))
    .filter((path) => {
      try {
        return statSync(path).isDirectory() && statSync(join(path, 'rollout.json')).isFile();
      } catch {
        return false;
      }
    });
}

function run(cmd, args, opts = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(cmd, args, { stdio: 'inherit', ...opts });
    child.on('exit', (code) => {
      if (code === 0) resolvePromise();
      else reject(new Error(`${cmd} exited ${code}`));
    });
  });
}

async function waitForServer(port) {
  for (let i = 0; i < 80; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/`);
      if (res.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`vite server did not start on ${port}`);
}

function freePort(start = 5190) {
  return new Promise((resolvePromise, reject) => {
    const tryPort = (port) => {
      const srv = createServer();
      srv.once('error', () => tryPort(port + 1));
      srv.once('listening', () => {
        srv.close(() => resolvePromise(port));
      });
      srv.listen(port, '127.0.0.1');
    };
    tryPort(start);
    setTimeout(() => reject(new Error('could not find a free port')), 5000);
  });
}

async function main() {
  const dirs = artifactDirs();
  if (!dirs.length) {
    console.log('no rollout artifacts found');
    return;
  }
  await run('npm', ['run', 'build'], { cwd: viz });
  const port = await freePort();
  const server = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: viz,
    stdio: ['ignore', 'inherit', 'inherit'],
  });
  try {
    await waitForServer(port);
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 960, height: 540 }, deviceScaleFactor: 1 });
    for (const dir of dirs) {
      const slug = basename(dir);
      const rolloutPublic = join(generated, `${slug}.json`);
      copyFileSync(join(dir, 'rollout.json'), rolloutPublic);
      const framesDir = join(generated, `${slug}_frames`);
      mkdirSync(framesDir, { recursive: true });
      const rolloutPath = '/' + relative(publicDir, rolloutPublic).split('/').join('/');
      await page.goto(`http://127.0.0.1:${port}/?rollout=${encodeURIComponent(rolloutPath)}`, { waitUntil: 'networkidle' });
      await page.waitForSelector('#scene');
      await page.waitForTimeout(900);
      for (let i = 0; i < 36; i++) {
        await page.locator('#scene').screenshot({ path: join(framesDir, `${String(i).padStart(3, '0')}.png`) });
        await page.waitForTimeout(95);
      }
      const gifPath = join(dir, 'mujoco_3d.gif');
      await run('python3', ['-c', `
from pathlib import Path
from PIL import Image
frames = sorted(Path(${JSON.stringify(framesDir)}).glob('*.png'))
imgs = [Image.open(p).convert('P', palette=Image.Palette.ADAPTIVE) for p in frames]
imgs[0].save(${JSON.stringify(gifPath)}, save_all=True, append_images=imgs[1:], duration=95, loop=0, optimize=True)
print(${JSON.stringify(gifPath)})
`]);
      console.log(`rendered ${gifPath}`);
    }
    await browser.close();
  } finally {
    server.kill('SIGTERM');
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
