import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";
import { dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Font sizes must come from the type scale in globals.css, never from an
// arbitrary Tailwind value. This is enforced rather than documented because the
// codebase had already accumulated 818 hardcoded sizes across 27 distinct
// values — including six pairs (12px/12.5px, 13px/13.5px, …) whose difference
// nobody can see but whose inconsistency is exactly what makes a UI read as
// unpolished. One `text-[13px]` slipped into review is how that starts again.
const NO_ARBITRARY_FONT_SIZE =
  'Use a type-scale step (text-2xs/xs/sm/base/md/lg/xl/2xl/3xl/4xl/5xl/6xl) instead of an ' +
  'arbitrary pixel size. The scale is defined in src/app/globals.css.';

const eslintConfig = [...nextCoreWebVitals, ...nextTypescript, {
  rules: {
    "no-restricted-syntax": ["error",
      // Covers className="…", cn('…'), and clsx object keys.
      { selector: "Literal[value=/text-\\[[0-9.]+px\\]/]", message: NO_ARBITRARY_FONT_SIZE },
      // Covers the `…${cond}…` template strings used throughout the sections.
      { selector: "TemplateElement[value.raw=/text-\\[[0-9.]+px\\]/]", message: NO_ARBITRARY_FONT_SIZE },
    ],

    // TypeScript rules
    "@typescript-eslint/no-explicit-any": "off",
    "@typescript-eslint/no-unused-vars": "off",
    "@typescript-eslint/no-non-null-assertion": "off",
    "@typescript-eslint/ban-ts-comment": "off",
    "@typescript-eslint/prefer-as-const": "off",
    "@typescript-eslint/no-unused-disable-directive": "off",
    
    // React rules
    "react-hooks/exhaustive-deps": "off",
    "react-hooks/purity": "off",
    "react/no-unescaped-entities": "off",
    "react/display-name": "off",
    "react/prop-types": "off",
    "react-compiler/react-compiler": "off",
    
    // Next.js rules
    "@next/next/no-img-element": "off",
    "@next/next/no-html-link-for-pages": "off",
    
    // General JavaScript rules
    "prefer-const": "off",
    "no-unused-vars": "off",
    "no-console": "off",
    "no-debugger": "off",
    "no-empty": "off",
    "no-irregular-whitespace": "off",
    "no-case-declarations": "off",
    "no-fallthrough": "off",
    "no-mixed-spaces-and-tabs": "off",
    "no-redeclare": "off",
    "no-undef": "off",
    "no-unreachable": "off",
    "no-useless-escape": "off",
  },
}, {
  ignores: ["node_modules/**", ".next/**", "out/**", "build/**", "next-env.d.ts", "examples/**", "skills"]
}];

export default eslintConfig;
