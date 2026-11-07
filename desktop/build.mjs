import {build} from 'esbuild';
import {mkdir,copyFile} from 'node:fs/promises';
await mkdir('dist',{recursive:true});
await build({entryPoints:['src/main.ts','src/preload.ts'],outdir:'dist',outExtension:{'.js':'.cjs'},bundle:true,platform:'node',external:['electron'],format:'cjs'});
await build({entryPoints:['src/app.tsx'],outfile:'dist/app.js',bundle:true,platform:'browser',format:'iife'});
await copyFile('src/index.html','dist/index.html');
