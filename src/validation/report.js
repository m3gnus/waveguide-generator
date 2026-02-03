export function generateSummaryMessage(errors, warnings) {
  if (errors.length === 0 && warnings.length === 0) {
    return 'All physical sanity checks passed';
  }
  if (errors.length > 0) {
    return `${errors.length} critical issues found: ${errors.map((e) => e.name).join(', ')}`;
  }
  return `${warnings.length} warnings: ${warnings.map((w) => w.name).join(', ')}`;
}

export function generateReport(report) {
  const lines = ['=== BEM Validation Report ===', ''];

  for (const [sectionName, section] of Object.entries(report.sections)) {
    lines.push(`## ${sectionName}`);
    lines.push(`Status: ${section.passed ? 'PASSED' : 'FAILED'} (${section.severity})`);
    lines.push(section.message);
    lines.push('');

    if (section.checks) {
      for (const check of section.checks) {
        const icon = check.passed ? '✓' : check.severity === 'error' ? '✗' : '⚠';
        lines.push(`  ${icon} ${check.name}: ${check.message}`);
      }
    }
    lines.push('');
  }

  lines.push(`Overall: ${report.overallPassed ? 'PASSED' : 'FAILED'}`);
  return lines.join('\n');
}
