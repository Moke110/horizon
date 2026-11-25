export function DictionaryIcon() {
  return <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
    {[2,11].flatMap(y=>[2,11].map(x=><rect key={x+':'+y} x={x} y={y} width="7" height="7" rx="1.8"/>))}
  </svg>;
}
