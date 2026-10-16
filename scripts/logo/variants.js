// Three candidate marks on a 24-unit grid. `ink` is the ink colour, `paper` the knockout halo.
const V = {
  A: (ink, paper) => `
    <circle cx="10" cy="14" r="7" stroke="${ink}" stroke-width="2"/>
    <path d="M15 9 L21.25 2.75" stroke="${ink}" stroke-width="1.25" stroke-linecap="round"/>
    <circle cx="21.25" cy="2.75" r="1.25" fill="${ink}"/>
    <circle cx="15" cy="9" r="3" fill="#E5484D" stroke="${paper}" stroke-width="1.25"/>`,
  B: (ink, paper) => `
    <ellipse cx="10.5" cy="13.25" rx="6.1" ry="8.25" stroke="${ink}" stroke-width="2.5"/>
    <path d="M15.6 7.4 L20.9 2.6" stroke="${ink}" stroke-width="1.75" stroke-linecap="round"/>
    <circle cx="20.9" cy="2.6" r="1.6" fill="${ink}"/>
    <circle cx="15.1" cy="7.75" r="3.5" fill="#E5484D" stroke="${paper}" stroke-width="1.6"/>`,
  C: (ink, paper) => `
    <circle cx="12" cy="12" r="8" stroke="${ink}" stroke-width="2.75"/>
    <circle cx="12" cy="12" r="1.9" fill="${ink}"/>
    <circle cx="17.66" cy="6.34" r="3.9" fill="#E5484D" stroke="${paper}" stroke-width="1.75"/>`,
};
module.exports = V;
