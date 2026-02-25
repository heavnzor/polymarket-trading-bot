'use client';

import { useQuery } from '@tanstack/react-query';
import { asPaginated, fetchMMQuotes, fetchMMInventory, fetchMMMetrics } from '@/lib/api';

export default function MMPage() {
  const { data: quotes, isLoading: loadingQuotes } = useQuery({
    queryKey: ['mm-quotes'],
    queryFn: () => fetchMMQuotes('active'),
    refetchInterval: 10000,
  });

  const { data: inventory, isLoading: loadingInv } = useQuery({
    queryKey: ['mm-inventory'],
    queryFn: fetchMMInventory,
    refetchInterval: 10000,
  });

  const { data: metrics } = useQuery({
    queryKey: ['mm-metrics'],
    queryFn: fetchMMMetrics,
    refetchInterval: 60000,
  });

  const quoteRows = quotes ? asPaginated(quotes) : [];
  const inventoryRows = inventory ? asPaginated(inventory) : [];
  const metricRows = metrics ? asPaginated(metrics) : [];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Market Making</h1>

      {/* Active Quotes */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-4">Active Quotes</h2>
        {loadingQuotes ? (
          <p className="text-gray-500">Loading...</p>
        ) : quoteRows.length === 0 ? (
          <p className="text-gray-500">No active quotes</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 px-3">Market</th>
                  <th className="text-right py-2 px-3">Bid</th>
                  <th className="text-right py-2 px-3">Ask</th>
                  <th className="text-right py-2 px-3">Spread</th>
                  <th className="text-right py-2 px-3">Size</th>
                  <th className="text-left py-2 px-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {quoteRows.map((q: any) => (
                  <tr key={q.id} className="border-b hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="py-2 px-3 font-mono text-xs">{q.market_id?.slice(0, 16)}...</td>
                    <td className="text-right py-2 px-3 text-green-600">{q.bid_price?.toFixed(2)}</td>
                    <td className="text-right py-2 px-3 text-red-600">{q.ask_price?.toFixed(2)}</td>
                    <td className="text-right py-2 px-3">{((q.ask_price - q.bid_price) * 100).toFixed(1)}pts</td>
                    <td className="text-right py-2 px-3">{q.size}</td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-xs ${
                        q.status === 'active' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'
                      }`}>
                        {q.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Inventory */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-4">Inventory</h2>
        {loadingInv ? (
          <p className="text-gray-500">Loading...</p>
        ) : inventoryRows.length === 0 ? (
          <p className="text-gray-500">No inventory</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 px-3">Market</th>
                  <th className="text-right py-2 px-3">Net Position</th>
                  <th className="text-right py-2 px-3">Avg Entry</th>
                  <th className="text-right py-2 px-3">Realized PnL</th>
                </tr>
              </thead>
              <tbody>
                {inventoryRows.map((inv: any) => (
                  <tr key={inv.id} className="border-b hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="py-2 px-3 font-mono text-xs">{inv.market_id?.slice(0, 16)}...</td>
                    <td className={`text-right py-2 px-3 ${inv.net_position > 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {inv.net_position?.toFixed(2)}
                    </td>
                    <td className="text-right py-2 px-3">{inv.avg_entry_price?.toFixed(4)}</td>
                    <td className={`text-right py-2 px-3 ${inv.realized_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      ${inv.realized_pnl?.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Daily Metrics */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-4">Daily Metrics</h2>
        {metricRows.length === 0 ? (
          <p className="text-gray-500">No metrics yet</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 px-3">Date</th>
                  <th className="text-right py-2 px-3">Fills</th>
                  <th className="text-right py-2 px-3">Round Trips</th>
                  <th className="text-right py-2 px-3">Gross PnL</th>
                  <th className="text-right py-2 px-3">Net PnL</th>
                  <th className="text-right py-2 px-3">Spread Capture</th>
                </tr>
              </thead>
              <tbody>
                {metricRows.map((m: any) => (
                  <tr key={m.id} className="border-b hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="py-2 px-3">{m.date}</td>
                    <td className="text-right py-2 px-3">{m.fills_count}</td>
                    <td className="text-right py-2 px-3">{m.round_trips}</td>
                    <td className={`text-right py-2 px-3 ${m.pnl_gross >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      ${m.pnl_gross?.toFixed(4)}
                    </td>
                    <td className={`text-right py-2 px-3 ${m.pnl_net >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      ${m.pnl_net?.toFixed(4)}
                    </td>
                    <td className="text-right py-2 px-3">{(m.spread_capture_rate * 100).toFixed(1)}%</td>
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
