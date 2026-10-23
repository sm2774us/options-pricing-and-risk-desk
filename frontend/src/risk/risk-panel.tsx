import {Position, RiskSummary} from './risk-math';

interface Props {
  risk: RiskSummary | null;
  positions: Position[];
  labels: string[];
  strikes: Float32Array | null;
  onClear(): void;
}

const money = (v: number) =>
  `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString('en-US', {maximumFractionDigits: 0})}`;
const sign = (v: number) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');

/** Live portfolio Greeks for the positions clicked into the chain. */
export function RiskPanel({risk, positions, labels, strikes, onClear}: Props) {
  return (
    <section className="panel risk" data-testid="risk">
      <h2>
        Risk <small>{positions.length} position(s)</small>
        <button className="link" onClick={onClear}>
          clear
        </button>
      </h2>
      {risk && positions.length > 0 ? (
        <>
          <div className="tiles">
            <div>
              <b className={sign(risk.pnl)} data-testid="risk-pnl">
                {money(risk.pnl)}
              </b>
              <span>P&amp;L vs entry</span>
            </div>
            <div>
              <b>{risk.delta.toFixed(1)}</b>
              <span>Δ shares</span>
            </div>
            <div>
              <b>{money(risk.dollarDelta)}</b>
              <span>$ delta</span>
            </div>
            <div>
              <b>{risk.gamma1pct.toFixed(1)}</b>
              <span>Δ per +1%</span>
            </div>
            <div>
              <b>{money(risk.vega)}</b>
              <span>vega / vol pt</span>
            </div>
            <div>
              <b>{money(risk.theta)}</b>
              <span>theta / day</span>
            </div>
          </div>
          <table className="scen">
            <tbody>
              <tr>
                {risk.scenarios.map(s => (
                  <th key={s.movePct}>
                    {s.movePct > 0 ? '+' : ''}
                    {s.movePct}%
                  </th>
                ))}
              </tr>
              <tr>
                {risk.scenarios.map(s => (
                  <td key={s.movePct} className={sign(s.pnl)}>
                    {money(s.pnl)}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
          <ul className="poslist">
            {positions.map(p => (
              <li key={`${p.expiryIdx}:${p.strikeIdx}:${p.side}`}>
                <b className={p.qty > 0 ? 'pos' : 'neg'}>
                  {p.qty > 0 ? '+' : ''}
                  {p.qty}
                </b>{' '}
                {labels[p.expiryIdx]}{' '}
                {strikes ? strikes[p.strikeIdx].toFixed(2) : ''}{' '}
                {p.side === 'C' ? 'Call' : 'Put'}
                <span> @ {p.entryPx.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="empty">
          Click a call or put price in the chain to open a position.
        </p>
      )}
    </section>
  );
}
