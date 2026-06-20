import { useEffect, useState } from 'react';

export async function api(path: string, options: RequestInit = {}): Promise<any> {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

export function cents(value: number | null | undefined): string {
  const amount = Number(value || 0);
  const sign = amount < 0 ? '-' : '';
  const absolute = Math.abs(amount);
  return `${sign}$${Math.floor(absolute / 100)}.${String(absolute % 100).padStart(2, '0')}`;
}

export function dollarsToCents(value: string | number): number {
  const parsed = Number.parseFloat(String(value));
  if (!Number.isFinite(parsed)) {
    return 0;
  }
  return Math.round(parsed * 100);
}

export function centsToDollars(value: number | null | undefined): string {
  return (Number(value || 0) / 100).toFixed(2);
}

export function paymentClass(amount: number): string {
  if (amount > 0) return 'pay';
  if (amount < 0) return 'receive';
  return '';
}

export function useAsync<T = any>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    setError('');
    load()
      .then((nextData) => {
        if (alive) setData(nextData);
      })
      .catch((err) => {
        if (alive) setError(err.message);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, setData, error };
}
