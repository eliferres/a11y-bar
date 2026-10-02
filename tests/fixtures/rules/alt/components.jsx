export const Gallery = ({ items, desc }) => (
  <ul>
    {items.map(i => <li key={i.id}><img src={i.src} /></li>)}
    <img alt={desc} src="cover.png" />
  </ul>
);
