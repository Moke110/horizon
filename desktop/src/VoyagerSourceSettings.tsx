import React, {useEffect, useState} from 'react';

const sources = [
  {id: 'openalex', label: 'OpenAlex', help: '使用已验证邮箱的 OpenAlex 账号创建 API Key。每次检索前会核对当日额度。'},
  {id: 'tavily', label: 'Tavily', help: '使用免费的 Researcher 套餐，关闭 PAYG，并为 API Key 设置有限的 Monthly Limit。'},
] as const;

type Source = typeof sources[number]['id'];
type SourceState = 'missing' | 'saved' | 'environment';
const statusText: Record<SourceState, string> = {
  missing: '未配置',
  saved: '已保存密钥',
  environment: '使用启动环境的密钥',
};

export function VoyagerSourceSettings() {
  const [status, setStatus] = useState<Record<Source, SourceState> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function load() {
    setBusy(true);
    setError('');
    window.horizon.send({action: 'voyager_source_settings'});
  }

  useEffect(() => {
    const unsubscribe = window.horizon.subscribe(event => {
      if (event.kind === 'voyager_source_settings') {
        setStatus(event.data);
        setBusy(false);
      }
      if (event.kind === 'voyager_source_settings_error') {
        setError(event.data);
        setBusy(false);
      }
    });
    load();
    return unsubscribe;
  }, []);

  function save(source: Source, key: string | null) {
    setBusy(true);
    setError('');
    window.horizon.send({action: 'voyager_save_source_key', source, key});
  }

  return (
    <details>
      <summary>检索来源 API</summary>
      <p>密钥保存在本机。下次检索时验证账号权限和额度。</p>
      {sources.map(source => (
        <form key={source.id} onSubmit={event => {
          event.preventDefault();
          const form = event.currentTarget;
          const key = new FormData(form).get('key');
          if (typeof key === 'string' && key.trim()) {
            save(source.id, key.trim());
            form.reset();
          }
        }}>
          <label>{source.label} API Key
            <input name="key" type="password" autoComplete="off" maxLength={1024} required disabled={busy || !status} />
          </label>
          <p>{source.help}</p>
          {status && <p role="status">{statusText[status[source.id]]}</p>}
          <button disabled={busy || !status}>保存 {source.label}</button>
          {status?.[source.id] === 'saved' &&
            <button type="button" disabled={busy} onClick={() => save(source.id, null)}>移除已保存的 {source.label} 密钥</button>}
        </form>
      ))}
      {error && <p role="alert">{error}</p>}
      {error && !status && <button type="button" disabled={busy} onClick={load}>重新读取配置</button>}
    </details>
  );
}
