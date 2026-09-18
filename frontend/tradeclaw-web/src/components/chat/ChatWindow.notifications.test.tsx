import { act, render, screen, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ChatWindow from './ChatWindow';

const ws = vi.hoisted(() => ({ listener: null as null | ((m: unknown) => void),
  connect: vi.fn(), disconnect: vi.fn(), ackDelivery: vi.fn() }));
vi.mock('@/lib/chatWs', () => ({ chatWS: { connect: ws.connect,
  disconnect: ws.disconnect, ackDelivery: ws.ackDelivery,
  onMessage: (cb: (m: unknown) => void) => {
    ws.listener = cb; return () => { ws.listener = null; };
  } } }));
beforeEach(() => {
  localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);
it('receives and persists notifications while chat is collapsed, without duplicates', () => {
  render(<ChatWindow />);
  expect(ws.connect).toHaveBeenCalled();
  expect(ws.listener).not.toBeNull();
  const message = { type: 'task_notification', data: 'controlled scheduled failure', delivery_id: 'notify-1' };
  act(() => { ws.listener!(message); ws.listener!(message); });
  const history = JSON.parse(localStorage.getItem('tradeclaw_chat_history') || '[]');
  expect(history.filter((m: { content: string }) => m.content === message.data)).toHaveLength(1);
});
it('renders JSON objects as a structured view and keeps plain text as text', async () => {
  render(<ChatWindow />);
  act(() => { screen.getByTitle('打开 Supervisor 对话').click(); });
  const jsonMsg = { type: 'task_notification', data: JSON.stringify({ status: 'done', score: 0.87, tags: ['a', 'b'] }), delivery_id: 'json-1' };
  const textMsg = { type: 'task_notification', data: '普通文本 { 不是 json', delivery_id: 'text-1' };
  act(() => { ws.listener!(jsonMsg); ws.listener!(textMsg); });
  // 字段名与值进入 DOM（React 文本节点可能拆分，用 findAllByText 聚合）
  expect((await screen.findAllByText(/status/)).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/score/)).length).toBeGreaterThan(0);
  expect(screen.getByText('普通文本 { 不是 json')).toBeDefined();
});
