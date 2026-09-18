import { act, render, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ChatWindow from './ChatWindow';

const ws = vi.hoisted(() => ({
  listener: null as null | ((m: unknown) => void),
  connect: vi.fn(),
  disconnect: vi.fn(),
  ackDelivery: vi.fn(),
}));
vi.mock('@/lib/chatWs', () => ({ chatWS: {
  connect: ws.connect,
  disconnect: ws.disconnect,
  ackDelivery: ws.ackDelivery,
  onMessage: (cb: (m: unknown) => void) => {
    ws.listener = cb;
    return () => { ws.listener = null; };
  },
} }));

beforeEach(() => {
  localStorage.clear();
  ws.ackDelivery.mockClear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

it('acks a chat_response by delivery_id so the server can clear its offline buffer', () => {
  render(<ChatWindow />);
  act(() => {
    ws.listener!({
      type: 'chat_response',
      data: 'report body',
      task_id: 'task-1',
      status: 'done',
      delivery_id: 'd-1',
    });
  });
  expect(ws.ackDelivery).toHaveBeenCalledWith('d-1');
});

it('does not duplicate a replayed chat_response with the same task_id', () => {
  render(<ChatWindow />);
  const msg = {
    type: 'chat_response',
    data: 'report body',
    task_id: 'task-2',
    status: 'done',
    delivery_id: 'd-2',
  };
  act(() => { ws.listener!(msg); });
  const count = (msgs: { content: string }[]) =>
    msgs.filter((m) => m.content === 'report body').length;
  const first = JSON.parse(localStorage.getItem('tradeclaw_chat_history') || '[]');
  expect(count(first)).toBe(1);
  // 未收到 ack 时服务端会在重连后补发同一条终态消息 —— 前端必须去重
  act(() => { ws.listener!(msg); });
  const second = JSON.parse(localStorage.getItem('tradeclaw_chat_history') || '[]');
  expect(count(second)).toBe(1);
});
