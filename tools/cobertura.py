#!/usr/bin/env python3
"""Mede a cobertura de LINHAS dos testes sem instalar nada (Python 3.12+, usa sys.monitoring da biblioteca padrão).

Uso (na pasta do CDBS):
    python3 tools/cobertura.py                 # todos os testes
    python3 tools/cobertura.py "test_bot*.py"  # só alguns
    python3 tools/cobertura.py --json saida.json

Limites (ditos no relatório): conta só o código executado DENTRO do processo dos testes. Código rodado em processos
filhos (scripts .sh, servidor iniciado como processo separado, supervisor real) não entra na conta, e o JavaScript
do painel também não (esse é coberto pelos testes de navegador, que não medem linhas).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def executable_lines(path: Path) -> set[int]:
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    lines: set[int] = set()
    stack = [code]
    while stack:
        current = stack.pop()
        lines.update(line for _, _, line in current.co_lines() if line is not None)
        stack.extend(const for const in current.co_consts if hasattr(const, "co_lines"))
    return lines


def main(argv: list[str]) -> int:
    if not hasattr(sys, "monitoring"):
        print("Precisa de Python 3.12 ou mais novo (sys.monitoring).", file=sys.stderr)
        return 2
    pattern = next((a for a in argv[1:] if not a.startswith("--") and not a.endswith(".json")), "test_*.py")
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    sources = sorted(p for p in ROOT.glob("*.py"))
    wanted = {str(p) for p in sources}
    hits: dict[str, set[int]] = {name: set() for name in wanted}
    mon = sys.monitoring
    tool = mon.COVERAGE_ID

    def on_line(code, line):                                   # vale para TODAS as threads do processo
        bucket = hits.get(code.co_filename)
        if bucket is not None:
            bucket.add(line)
        return mon.DISABLE                                     # cada linha só precisa ser vista uma vez: fica rápido

    mon.use_tool_id(tool, "cdbs-cobertura")
    mon.register_callback(tool, mon.events.LINE, on_line)
    mon.set_events(tool, mon.events.LINE)
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern, top_level_dir=str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    mon.set_events(tool, 0)
    mon.free_tool_id(tool)

    rows = []
    total_exec = total_hit = 0
    for path in sources:
        lines = executable_lines(path)
        hit = len(hits[str(path)] & lines)
        total_exec += len(lines)
        total_hit += hit
        rows.append({"file": path.name, "lines": len(lines), "hit": hit, "percent": round(100.0 * hit / len(lines), 1) if lines else 100.0})
    report = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
              "files": rows, "total": {"lines": total_exec, "hit": total_hit, "percent": round(100.0 * total_hit / max(total_exec, 1), 1)}}
    width = max(len(r["file"]) for r in rows)
    print(f"{'arquivo'.ljust(width)}  linhas  cobertas      %")
    for row in rows:
        print(f"{row['file'].ljust(width)}  {row['lines']:6d}  {row['hit']:8d}  {row['percent']:5.1f}")
    t = report["total"]
    print(f"{'TOTAL'.ljust(width)}  {t['lines']:6d}  {t['hit']:8d}  {t['percent']:5.1f}")
    print(f"testes: {result.testsRun} · falhas: {len(result.failures)} · erros: {len(result.errors)} · pulados: {len(result.skipped)}")
    if out_json:
        Path(out_json).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
