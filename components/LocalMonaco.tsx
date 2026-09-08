'use client';

import Editor, { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';

loader.config({ monaco });

export function LocalMonaco({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <Editor
      height="460px"
      defaultLanguage="markdown"
      theme="vs-dark"
      value={value}
      onChange={(next) => onChange(next ?? '')}
      options={{
        minimap: { enabled: false },
        wordWrap: 'on',
        fontSize: 14,
        lineHeight: 23,
        padding: { top: 18 },
        scrollBeyondLastLine: false,
        automaticLayout: true
      }}
    />
  );
}
