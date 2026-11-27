import React, {type ReactNode} from 'react';

const contentTags=new Set([
  'article','section','header','footer','main','div','span','p','br','hr','h1','h2','h3','h4','h5','h6',
  'strong','b','em','i','u','small','s','sub','sup','code','pre','blockquote','ul','ol','li','dl','dt','dd',
  'table','thead','tbody','tfoot','tr','th','td','caption','figure','figcaption','img','details','summary','time',
]);
const inactiveTags=new Set(['script','style','template','noscript','iframe','object','embed','title']);

function renderNode(node:Node,key:number):ReactNode {
  if(node.nodeType===Node.TEXT_NODE)return node.textContent;
  if(!(node instanceof Element))return null;
  const tag=node.localName;
  if(inactiveTags.has(tag))return null;
  if(tag==='img'){
    const src=node.getAttribute('src')??'';
    return /^data:image\/(?:png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=]+$/i.test(src)
      ? <img key={key} src={src} alt={node.getAttribute('alt')??''}/>
      : node.getAttribute('alt');
  }
  const children=Array.from(node.childNodes,renderNode);
  if(tag==='hr'||tag==='br')return React.createElement(tag,{key});
  return contentTags.has(tag)
    ? React.createElement(tag,{key},children)
    : <React.Fragment key={key}>{children}</React.Fragment>;
}

export async function readLocalHtml(file:File):Promise<ReactNode> {
  if(!/\.html?$/i.test(file.name)||file.size>10*1024*1024)throw Error('Invalid HTML file');
  return renderLocalHtml(await file.text());
}

export function renderLocalHtml(html:string):ReactNode {
  // Template content stays inert. The preview only creates static elements with approved attributes.
  const template=document.createElement('template');
  template.innerHTML=html;
  return Array.from(template.content.childNodes,renderNode);
}
