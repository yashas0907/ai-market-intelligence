const { execSync } = require("child_process")
const fs = require("fs")
const path = require("path")

// Lightweight no-undef / no-unused-vars check for the frontend without a full eslint setup:
// parse each JSX/JS file's declared identifiers and flag obvious undefined references.
// (Complements vite build, which does not catch undefined variables.)
const srcDir = path.join(__dirname, "..", "src")
const files = []
function walk(dir) {
  for (const f of fs.readdirSync(dir)) {
    const p = path.join(dir, f)
    if (fs.statSync(p).isDirectory()) walk(p)
    else if (/\.(jsx?|js)$/.test(f)) files.push(p)
  }
}
walk(srcDir)

let failed = false
for (const file of files) {
  const code = fs.readFileSync(file, "utf-8")
  // collect declared identifiers: function/const/let/var names, params of the outer components
  const declared = new Set()
  const declRe = /(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)/g
  let m
  while ((m = declRe.exec(code))) declared.add(m[1])
  // import bindings
  const importRe = /import\s+(?:([A-Za-z_$][\w$]*)|\{([^}]+)\})\s+from/g
  while ((m = importRe.exec(code))) {
    if (m[1]) declared.add(m[1])
    if (m[2]) m[2].split(",").forEach((part) => {
      const name = part.trim().split(/\s+as\s+/).pop().trim()
      if (name) declared.add(name)
    })
  }
  // component/function params (single destructure line after "function X(")
  const fnRe = /function\s+[A-Za-z_$][\w$]*\s*\(\{([^}]+)\}\)/g
  while ((m = fnRe.exec(code))) {
    m[1].split(",").forEach((part) => {
      const name = part.trim()
      if (name) declared.add(name)
    })
  }
  // built-ins + JSX globals
  for (const b of ["React", "useState", "useEffect", "useRef", "useCallback", "useSearchParams", "window", "document", "console", "JSON", "Math", "Date", "Object", "Set", "Number", "String", "Boolean", "Array", "undefined", "null", "true", "false", "NaN", "EventSource", "FormData", "URLSearchParams", "localStorage", "setTimeout", "setInterval", "clearInterval", "clearTimeout", "Intl", "Promise", "Error", "isFinite", "parseInt", "parseFloat", "encodeURIComponent", "decodeURIComponent"]) declared.add(b)

  // find identifier usages in JSX expressions { ... } and JS code — flag obvious undefined ones
  const useRe = /\b([a-z_$][\w$]*)\s*(?:\.|\()/g
  const lines = code.split("\n")
  lines.forEach((line, idx) => {
    let u
    const re = new RegExp(useRe.source, "g")
    while ((u = re.exec(line))) {
      const name = u[1]
      if (declared.has(name)) continue
      if (/^(import|from|export|default|return|if|else|for|while|of|in|new|typeof|catch|try|switch|case|break|continue|const|let|var|function|async|await|class|extends|super|this|do|throw|delete|void|instanceof|yield)$/.test(name)) continue
      // property access on something (obj.prop) — only flag if the base is a bare unknown identifier at line start of JSX usage
      console.log(`POSSIBLE UNDEFINED: ${path.basename(file)}:${idx + 1}  '${name}'  |  ${line.trim().slice(0, 90)}`)
      failed = true
    }
  })
}
if (!failed) console.log("no-undef scan: clean")
process.exit(failed ? 1 : 0)
