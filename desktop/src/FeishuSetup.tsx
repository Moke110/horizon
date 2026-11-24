import React, {useEffect, useState} from 'react';

type State = {
  installation: {status:'installed'|'invalid'|'missing'; version:string; source:string; archive_sha256:string};
  credentials: 'unconfigured'|'configured'|'unreadable';
  console_url: string;
  required_scopes: string[];
  permission_check?: {status:'bot_verified'|'unverified'|'missing_scopes'; missing_scopes:string[]};
  authorization?: {url:string; png:string};
};

export function FeishuSetup({send, close}: {send:(command:unknown)=>void; close:()=>void}) {
  const [state, setState] = useState<State|null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [authorization, setAuthorization] = useState<State['authorization']>();
  const [permission, setPermission] = useState<State['permission_check']>();
  useEffect(() => {
    const release = window.horizon.subscribe(event=>{
      if (event.kind==='feishu_adapter') {
        setState(event.data);
        if (event.data.authorization) setAuthorization(event.data.authorization);
        if (event.data.permission_check) setPermission(event.data.permission_check);
        setBusy(false);
      }
      if (event.kind==='feishu_adapter_error') {
        setError(event.data.message);
        setBusy(false);
      }
    });
    send({action:'feishu_status'});
    return release;
  }, [send]);
  function run(action:string, options?:{app_id:string; secret:string}) {
    setBusy(true);
    setError('');
    send({action, ...options});
  }
  const install = state?.installation;
  const installed = install?.status==='installed';
  const configured = state?.credentials==='configured';
  return <section className="messages" aria-label="飞书适配器">
    <header><h1>飞书适配器</h1><button onClick={close}>返回对话</button></header>
    {error && <p role="alert">{error}</p>}
    {busy && <p role="status">处理中…</p>}
    <section>
      <h2>安装官方 CLI</h2>
      {install && <p>版本 {install.version} · {installed?'已校验安装':install.status==='invalid'?'本地安装校验失败':'未安装'}</p>}
      {install && <details><summary>来源与校验值</summary><p>{install.source}</p><p>ZIP SHA-256 <code>{install.archive_sha256}</code></p></details>}
      <button disabled={busy} onClick={()=>run('feishu_install')}>{installed?'重新校验':'安装或重试'}</button>
    </section>
    <section>
      <h2>应用凭据</h2>
      <p>{configured?'已保存应用凭据。':state?.credentials==='unreadable'?'无法读取凭据，请重新保存。':'用飞书开发者后台的企业自建应用凭据配置机器人。'}</p>
      <form onSubmit={event=>{
        event.preventDefault();
        const form=event.currentTarget;
        const data=new FormData(form);
        form.reset();
        setPermission(undefined);
        setAuthorization(undefined);
        run('feishu_configure',{app_id:String(data.get('appId') || '').trim(), secret:String(data.get('secret') || '')});
      }}>
        <label>App ID <input name="appId" required autoComplete="off" disabled={!installed || busy}/></label>
        <label>App Secret <input name="secret" required type="password" autoComplete="off" disabled={!installed || busy}/></label>
        <button type="submit" disabled={!installed || busy}>保存应用凭据</button>
      </form>
    </section>
    <section>
      <h2>权限</h2>
      <p>打开开发者后台，在应用的「开发配置 → 权限管理」申请下列应用权限并发布版本。</p>
      <ul>{state?.required_scopes.map(scope=><li key={scope}><code>{scope}</code></li>)}</ul>
      {state?.console_url && <p>授权入口 <code>{state.console_url}</code> <button onClick={()=>
        navigator.clipboard.writeText(state.console_url).catch(()=>setError('无法复制链接，可选择上方文字复制。'))}>复制链接</button></p>}
      <button disabled={!installed || busy} onClick={()=>run('feishu_qr')}>显示授权二维码</button>
      {authorization?.url===state?.console_url && authorization && <p><img src={`data:image/png;base64,${authorization.png}`} width={192} height={192} alt="飞书开发者后台授权入口二维码"/></p>}
      <button disabled={!installed || !configured || busy} onClick={()=>run('feishu_check')}>检查机器人身份</button>
      {permission?.status==='bot_verified' && <p>已确认机器人身份。事件接收和消息发送权限仍需按实际使用验证。</p>}
      {permission?.status==='unverified' && <p>机器人身份待验证。检查凭据、应用发布状态和网络后重试。</p>}
      {permission?.status==='missing_scopes' && <p>需在权限管理中开通并发布 <code>{permission.missing_scopes.join(' · ')}</code></p>}
    </section>
    <section><h2>消息订阅</h2><p>待验证。在「事件与回调 → 事件配置」选择长连接，添加 <code>im.message.receive_v1</code>。</p></section>
    <section><h2>卡片回调</h2><p>待验证。在「事件与回调 → 回调配置」选择长连接，添加 <code>card.action.trigger</code>。</p></section>
    <section><h2>私聊绑定</h2><p>待绑定。将应用可用范围限制为使用该 Horizon 的探索者，并使用机器人的单人私聊。</p></section>
  </section>;
}
