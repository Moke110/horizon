import {build} from 'esbuild';
import {mkdir,copyFile,cp} from 'node:fs/promises';
await mkdir('dist',{recursive:true});
await build({entryPoints:['src/main.ts','src/preload.ts'],outdir:'dist',outExtension:{'.js':'.cjs'},bundle:true,platform:'node',external:['electron'],format:'cjs'});
await build({entryPoints:['src/app.tsx'],outfile:'dist/app.js',bundle:true,platform:'browser',format:'iife'});
await copyFile('src/index.html','dist/index.html');

await copyFile('node_modules/pdfjs-dist/build/pdf.min.mjs','dist/pdf.mjs');
await copyFile('node_modules/pdfjs-dist/build/pdf.worker.min.mjs','dist/pdf.worker.mjs');
await copyFile('node_modules/pdfjs-dist/LICENSE','dist/PDFjs-LICENSE');
for(const assets of ['standard_fonts','cmaps'])await cp('node_modules/pdfjs-dist/'+assets,'dist/'+assets,{recursive:true});
