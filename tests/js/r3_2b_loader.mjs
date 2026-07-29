import { pathToFileURL } from "node:url";
import path from "node:path";

const root = process.env.SIM_INTENT_ROOT;

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("/static/")) {
    return {
      url: pathToFileURL(path.join(root, "app", specifier.slice(1))).href,
      shortCircuit: true,
    };
  }
  return nextResolve(specifier, context);
}
