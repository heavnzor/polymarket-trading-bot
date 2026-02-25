'use client';

import { useQuery } from '@tanstack/react-query';
import { asPaginated, fetchCDSignals } from '@/lib/api';

export default function CDPage() {
  const { data: signals, isLoading } = useQuery({
    queryKey: ['cd-signals'],
    queryFn: fetchCDSignals,
    refetchInterval: 30000,
  });

  const signalRows = signals ? asPaginated(signals) : [];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Crypto Directional</h1>

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-4">Recent Signals</h2>
        {isLoading ? (
          <p className="text-gray-500">Loading...</p>
        ) : signalRows.length === 0 ? (
          <p className="text-gray-500">No signals yet</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 px-3">Coin</th>
                  <th className="text-right py-2 px-3">Strike</th>
                  <th className="text-right py-2 px-3">Spot</th>
                  <th className="text-right py-2 px-3">Expiry</th>
                  <th className="text-right py-2 px-3">P(model)</th>
                  <th className="text-right py-2 px-3">P(market)</th>
                  <th className="text-right py-2 px-3">Edge</th>
                  <th className="text-right py-2 px-3">Conf</th>
                  <th className="text-left py-2 px-3">Action</th>
                  <th className="text-right py-2 px-3">Size</th>
                </tr>
              </thead>
              <tbody>
                {signalRows.map((s: any) => (
                  <tr key={s.id} className="border-b hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="py-2 px-3 font-semibold">{s.coin}</td>
                    <td className="text-right py-2 px-3">${s.strike?.toLocaleString()}</td>
                    <td className="text-right py-2 px-3">${s.spot_price?.toLocaleString()}</td>
                    <td className="text-right py-2 px-3">{s.expiry_days?.toFixed(1)}d</td>
                    <td className="text-right py-2 px-3">{(s.p_model * 100).toFixed(1)}%</td>
                    <td className="text-right py-2 px-3">{(s.p_market * 100).toFixed(1)}%</td>
                    <td className={`text-right py-2 px-3 font-semibold ${
                      s.edge_pts >= 5 ? 'text-green-600' : s.edge_pts >= 3 ? 'text-yellow-600' : 'text-gray-500'
                    }`}>
                      {s.edge_pts?.toFixed(1)}pts
                    </td>
                    <td className="text-right py-2 px-3">{s.confirmation_count}</td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-xs ${
                        s.action === 'trade' ? 'bg-green-100 text-green-800' :
                        s.action === 'confirming' ? 'bg-yellow-100 text-yellow-800' :
                        'bg-gray-100 text-gray-600'
                      }`}>
                        {s.action}
                      </span>
                    </td>
                    <td className="text-right py-2 px-3">
                      {s.size_usdc ? `$${s.size_usdc.toFixed(2)}` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
