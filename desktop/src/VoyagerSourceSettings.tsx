import {TextInput} from './gui/elements/FormElements';
import {Checkbox} from './gui/elements/ChoiceElements';
import {Collapsible} from './gui/elements/Collapsible';
import React, {useEffect, useState} from 'react';

const sources = [
  {id: 'openalex', label: 'OpenAlex', field: 'API Key', help: '使用已验证邮箱的 OpenAlex 账号创建 API Key。每次检索前会核对当日额度。'},
  {id: 'tavily', label: 'Tavily', field: 'API Key', help: '使用免费的 Researcher 套餐，关闭 PAYG，并为 API Key 设置有限的 Monthly Limit。'},
  {id: 'unpaywall', label: 'Unpaywall', field: '联系邮箱', help: '可选。Unpaywall 用邮箱识别开放全文查询的调用方。'},
] as const;

type Source = typeof sources[number]['id'];
type SourceState = 'missing' | 'saved' | 'environment';
type SourceSettings = Partial<Record<Source, SourceState>> & {tavily_monthly_limit?: number};
const statusText: Record<SourceState, string> = {
  missing: '未配置',
  saved: '已保存密钥',
  environment: '使用启动环境的密钥',
};

export function VoyagerSourceSettings() {
  const [status, setStatus] = useState<SourceSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [paygoDisabled, setPaygoDisabled] = useState(false);
  const [monthlyLimit, setMonthlyLimit] = useState('');

  function load() {
    setBusy(true);
    setError('');
    window.horizon.send({action: 'voyager_source_settings'});
  }

  useEffect(() => {
    const unsubscribe = window.horizon.subscribe(event => {
      if (event.kind === 'voyager_source_settings') {
        setStatus(event.data);
        const cap = event.data.tavily_monthly_limit;
        setPaygoDisabled(cap !== undefined);
        setMonthlyLimit(cap === undefined ? '' : String(cap));
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

  function saveTavilyBudget(limit: number | null) {
    setBusy(true);
    setError('');
    window.horizon.send({action: 'voyager_save_tavily_budget', paygo_disabled: limit !== null, monthly_limit: limit});
  }

  return (
    <Collapsible title="检索来源 API">
      <p>密钥和联系邮箱保存在本机。使用来源时验证权限和额度。</p>
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
          <label>{source.label} {source.field}
            <TextInput name="key" type="password" autoComplete="off" maxLength={1024} required disabled={busy || !status} />
          </label>
          <p>{source.help}</p>
          {status && <p role="status">{status[source.id] === 'saved' && source.id === 'unpaywall' ? '已保存邮箱' : statusText[status[source.id] ?? 'missing']}</p>}
          <button disabled={busy || !status}>保存 {source.label}</button>
          {status?.[source.id] === 'saved' &&
            <button type="button" disabled={busy} onClick={() => save(source.id, null)}>移除 {source.label} {source.field}</button>}
        </form>
      ))}
      {status?.tavily === 'saved' && (
        <form onSubmit={event => {
          event.preventDefault();
          if (paygoDisabled && Number.isSafeInteger(Number(monthlyLimit)) && Number(monthlyLimit) > 0) {
            saveTavilyBudget(Number(monthlyLimit));
          }
        }}>
          <h3>Tavily 免费预算</h3>
          <p>如果 Tavily 接口未返回上限，可填写控制台中的设置。每次搜索仍会核对免费套餐剩余额度。</p>
          <Checkbox checked={paygoDisabled} disabled={busy}
              onChange={event => setPaygoDisabled(event.target.checked)}>
            我已在 Tavily 控制台关闭按量付费（PAYG）
          </Checkbox>
          <label>月度上限（credits）
            <TextInput type="number" min={1} max={Number.MAX_SAFE_INTEGER} step={1} required disabled={busy}
              value={monthlyLimit} onChange={event => setMonthlyLimit(event.target.value)} />
          </label>
          <button disabled={busy || !paygoDisabled}>保存 Tavily 免费预算</button>
          {status.tavily_monthly_limit !== undefined && (
            <button type="button" disabled={busy} onClick={() => saveTavilyBudget(null)}>清除确认与月度上限</button>
          )}
        </form>
      )}
      {error && <p role="alert">{error}</p>}
      {error && !status && <button type="button" disabled={busy} onClick={load}>重新读取配置</button>}
    </Collapsible>
  );
}
