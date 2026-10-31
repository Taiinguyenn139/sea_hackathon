import { ArrowUpRight, Sparkles } from "lucide-react";

/** Decorative artwork only; never represents a customer's balance or card. */
export function BankingScene() {
  return (
    <div className="banking-scene" aria-hidden="true">
      <div className="scene-grid" />
      <div className="scene-orbit orbit-back" />
      <div className="scene-orbit orbit-front" />
      <div className="scene-sphere" />
      <div className="illustrated-card">
        <div className="art-card-top"><strong>TKT<span>®</span></strong><Sparkles size={23} /></div>
        <div className="art-card-chip"><i /><i /><i /></div>
        <div className="art-card-bottom"><span>PERSONAL<br />BANKING</span><span className="card-circles"><i /><i /></span></div>
      </div>
      <div className="scene-sticker"><ArrowUpRight size={24} /></div>
      <div className="scene-caption"><span /> CHUYỆN CỦA BẠN. CHÚNG TÔI LẮNG NGHE.</div>
    </div>
  );
}

export function ServiceArt({ kind }: { kind: number }) {
  return (
    <svg className="service-art" viewBox="0 0 180 150" fill="none" aria-hidden="true">
      {kind === 0 ? (
        <>
          <ellipse cx="92" cy="130" rx="72" ry="12" fill="#203d3420" />
          <path d="M35 73 86 40 144 68 94 102Z" fill="#a8b8a0" />
          <path d="m35 73 59 29v29l-59-28Z" fill="#e6eadb" />
          <path d="m94 102 50-34v30l-50 33Z" fill="#c9d3bd" />
          <path d="M45 65 86 29l47 22-40 39Z" fill="#253e34" />
          <path d="m86 29 7 61 40-39Z" fill="#3f5b47" />
          <path d="m53 87 15 7v22l-15-7Z" fill="#788e6e" />
          <path d="m106 99 19-12v14l-19 12Z" fill="#6a8568" />
          <path d="m115 94 1 14M106 106l19-12" stroke="#c9d3bd" strokeWidth="2" />
          <path d="M150 108V70" stroke="#536d48" strokeWidth="4" />
          <ellipse cx="150" cy="62" rx="15" ry="23" fill="#819661" />
          <path d="m145 110 10-7" stroke="#34503d" strokeWidth="2" />
        </>
      ) : kind === 1 ? (
        <>
          <ellipse cx="93" cy="131" rx="64" ry="10" fill="#5b433018" />
          <path d="m37 96 39-20 38 20-40 21Z" fill="#d29c71" />
          <path d="m37 96 37 21v18l-37-20Z" fill="#be835c" />
          <path d="m74 117 40-21v18l-40 21Z" fill="#a26c48" />
          <path d="m80 60 39-21 33 19-38 22Z" fill="#f2cba2" />
          <path d="m80 60 34 20v38L80 98Z" fill="#e1b086" />
          <path d="m114 80 38-22v40l-38 20Z" fill="#bf8d66" />
          <circle cx="56" cy="51" r="23" fill="#e2b178" />
          <circle cx="54" cy="48" r="23" fill="#f7d8a7" />
          <circle cx="54" cy="48" r="16" stroke="#c99557" />
          <path d="M47 48h14m-7-7v14" stroke="#b98247" strokeWidth="2" />
          <path d="m123 20 7-9m14 22 10-1" stroke="#a47b58" strokeWidth="2" strokeLinecap="round" />
        </>
      ) : (
        <>
          <ellipse cx="92" cy="130" rx="66" ry="10" fill="#34325318" />
          <g transform="rotate(-17 90 75)">
            <rect x="36" y="22" width="105" height="69" rx="10" fill="#bab7d5" />
            <path d="M36 38h105v13H36Z" fill="#9290b0" />
          </g>
          <g transform="rotate(9 90 80)">
            <rect x="29" y="59" width="120" height="77" rx="11" fill="#474762" />
            <rect x="42" y="79" width="21" height="16" rx="4" fill="#e2cfb2" />
            <path d="M49 79v16m-7-8h21" stroke="#b9a586" />
            <path d="M43 115h35" stroke="#d5d1e8" strokeWidth="3" strokeLinecap="round" />
            <circle cx="121" cy="115" r="9" fill="#ddd8ee" />
            <circle cx="132" cy="115" r="9" fill="#a9a2c5" fillOpacity=".8" />
          </g>
        </>
      )}
    </svg>
  );
}
