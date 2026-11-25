import {FixedSelect} from './gui/elements/ChoiceElements';
import React, {useState} from 'react';
import {MainPanelDialog, MultilineInput, TextInput, VariableList} from './gui/elements/FormElements';

export type SourceInput = {material_id: string; locator: string; excerpt: string};
type SourceEdit = {kind: 'source'; index: number; value: SourceInput};
type RelationEdit = {kind: 'relation'; index: number; value: string};

function replace<T>(items: T[], index: number, value: T | null) {
  if (value === null) return items.filter((_, i) => i !== index);
  if (index === items.length) return [...items, value];
  return items.map((item, i) => i === index ? value : item);
}

export function TermLists({aliases, sources, relations, materials, terms, materialName, termName, onAliases, onSources, onRelations}: {
  aliases: string[];
  sources: SourceInput[];
  relations: string[];
  materials: {material_id: string; title: string}[];
  terms: {card_id: string; title: string; status: string}[];
  materialName: (id: string) => string;
  termName: (id: string) => string;
  onAliases: (items: string[]) => void;
  onSources: (items: SourceInput[]) => void;
  onRelations: (items: string[]) => void;
}) {
  const [alias, setAlias] = useState<{index: number; value: string} | null>(null);
  const [dialog, setDialog] = useState<SourceEdit | RelationEdit | null>(null);
  const [error, setError] = useState('');
  const saveAlias = () => {
    if (!alias) return;
    onAliases(replace(aliases, alias.index, alias.value.trim() || null));
    setAlias(null);
  };
  const openSource = (index: number) => {
    setError('');
    setDialog({kind: 'source', index, value: sources[index] ?? {material_id: '', locator: '', excerpt: ''}});
  };
  const openRelation = (index: number) => setDialog({kind: 'relation', index, value: relations[index] ?? ''});
  return <>
    <VariableList label="别名" items={aliases} onAdd={() => setAlias({index: aliases.length, value: ''})}
      onEdit={index => setAlias({index, value: aliases[index]})}
      onDelete={index => onAliases(replace(aliases, index, null))} editing={alias?.index} adding={alias?.index === aliases.length} onOutside={alias ? saveAlias : undefined}
      editor={alias && <TextInput autoFocus aria-label="编辑别名" value={alias.value}
        onChange={event => setAlias({...alias, value: event.target.value})}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === 'Enter' || event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            if (event.key === 'Enter') saveAlias();
            else setAlias(null);
          }
        }}/>}
    />
    <VariableList label="来源" items={sources.map(source => [materialName(source.material_id), source.locator, source.excerpt].filter(Boolean).join(' · '))}
      onAdd={() => openSource(sources.length)} addDisabled={sources.length >= 16} onEdit={openSource}
      onDelete={index => onSources(replace(sources, index, null))}/>
    <VariableList label="关联术语" items={relations.map(termName)} onAdd={() => openRelation(relations.length)} onEdit={openRelation}
      onDelete={index => onRelations(replace(relations, index, null))}/>
    {dialog?.kind === 'source' && <MainPanelDialog title="编辑来源" onCancel={() => setDialog(null)} onSave={() => {
      const {material_id, locator, excerpt} = dialog.value;
      if (!material_id && (locator.trim() || excerpt.trim())) {
        setError('请选择资料。');
        return;
      }
      onSources(replace(sources, dialog.index, material_id ? dialog.value : null));
      setDialog(null);
    }}>
      <label>已保存的资料<FixedSelect value={dialog.value.material_id} onChange={event => {
        setDialog({...dialog, value: {...dialog.value, material_id: event.target.value}});
        setError('');
      }}>
        <option value="">选择资料</option>
        {dialog.value.material_id && !materials.some(material => material.material_id === dialog.value.material_id)
          && <option value={dialog.value.material_id}>{materialName(dialog.value.material_id)}</option>}
        {materials.map(material => <option key={material.material_id} value={material.material_id}>{material.title}</option>)}
      </FixedSelect></label>
      <label>定位<TextInput value={dialog.value.locator} onChange={event => setDialog({...dialog, value: {...dialog.value, locator: event.target.value}})}/></label>
      <label>原文摘录<MultilineInput value={dialog.value.excerpt} onChange={event => setDialog({...dialog, value: {...dialog.value, excerpt: event.target.value}})}/></label>
      {error && <p role="alert">{error}</p>}
    </MainPanelDialog>}
    {dialog?.kind === 'relation' && <MainPanelDialog title="编辑关联术语" onCancel={() => setDialog(null)} onSave={() => {
      onRelations(replace(relations, dialog.index, dialog.value || null));
      setDialog(null);
    }}>
      <label>关联术语<FixedSelect value={dialog.value} onChange={event => setDialog({...dialog, value: event.target.value})}>
        <option value="">选择术语</option>
        {dialog.value && !terms.some(term => term.card_id === dialog.value)
          && <option value={dialog.value}>{termName(dialog.value)}</option>}
        {terms.filter(term => term.card_id === dialog.value || !relations.includes(term.card_id))
          .map(term => <option key={term.card_id} value={term.card_id}>{term.title}{term.status === 'archived' ? '（已归档）' : ''}</option>)}
      </FixedSelect></label>
    </MainPanelDialog>}
  </>;
}
