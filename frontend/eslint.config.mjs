import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // React Compiler 级别的严格规则：现有代码（shadcn/ui 官方组件、localStorage
      // 水合、主题/媒体查询监听等惯用模式）广泛命中，重构风险大于收益，降为警告
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/purity": "warn",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // openapi-ts 自动生成的 API client，重新生成会覆盖手改内容，不做 lint
    "src/client/**",
  ]),
]);

export default eslintConfig;
