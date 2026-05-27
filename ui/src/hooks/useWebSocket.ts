import { useRef, useState, useCallback, useEffect } from 'react';
import type { WsServerMessage } from '../types';

export type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export function useWebSocket(sessionId: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<WsStatus>('disconnected');
  const onMessageRef = useRef<((msg: WsServerMessage) => void) | null>(null);

  const connect = useCallback(() => {
    if (!sessionId) return;
    if (wsRef.current) {
      wsRef.current.close();
    }

    setStatus('connecting');
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = localStorage.getItem('db_clinic_token') || '';
    const wsUrl = token
      ? `${protocol}//${window.location.host}/ws/chat/${sessionId}?token=${encodeURIComponent(token)}`
      : `${protocol}//${window.location.host}/ws/chat/${sessionId}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => setStatus('connected');
    ws.onclose = () => setStatus('disconnected');
    ws.onerror = () => setStatus('error');
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data) as WsServerMessage;
        onMessageRef.current?.(msg);
      } catch {
        // ignore malformed messages
      }
    };

    wsRef.current = ws;
  }, [sessionId]);

  const send = useCallback((data: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setStatus('disconnected');
  }, []);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [sessionId]);

  return { status, connect, send, disconnect, setOnMessage: (fn: (msg: WsServerMessage) => void) => { onMessageRef.current = fn; } };
}
