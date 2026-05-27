import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import { api, setToken, clearToken } from '../api';
import type { User } from '../types';

const STORAGE_KEY = 'db_clinic_user';

interface UserContextValue {
  user: User | null;
  /** Creates a new user (username+password) via /api/auth/register */
  register: (username: string, password: string) => Promise<void>;
  /** Logs in via /api/auth/login — supports username-only or username+password */
  login: (username: string) => Promise<User>;
  logout: () => void;
}

const UserContext = createContext<UserContextValue | null>(null);

export function UserProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? (JSON.parse(raw) as User) : null;
    } catch {
      return null;
    }
  });

  // Persist to localStorage whenever user changes
  useEffect(() => {
    if (user) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  }, [user]);

  const register = useCallback(async (username: string, password: string) => {
    const res = await api<{ access_token: string }>('/api/auth/register', {
      method: 'POST',
      body: { username, password },
    });
    setToken(res.access_token);
  }, []);

  const login = useCallback(async (username: string): Promise<User> => {
    const res = await api<{ access_token: string }>('/api/auth/login', {
      method: 'POST',
      body: { username, password: '' },
    });
    setToken(res.access_token);
    // Decode the user from the JWT
    const me = await api<{ id: string; username: string }>('/api/auth/me');
    const u: User = { id: String(me.id), username: me.username };
    setUser(u);
    return u;
  }, []);

  const logout = useCallback(() => {
    setUser(null);
    clearToken();
  }, []);

  return (
    <UserContext.Provider value={{ user, register, login, logout }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser(): UserContextValue {
  const ctx = useContext(UserContext);
  if (!ctx) throw new Error('useUser must be used within UserProvider');
  return ctx;
}
