(() => {
  const list = document.getElementById("person-list");
  const search = document.getElementById("search");
  const sort = document.getElementById("sort");
  const resultCount = document.getElementById("result-count");

  if (!list || !search || !sort || !resultCount) {
    return;
  }

  const rows = Array.from(list.querySelectorAll(".person-row"));

  function getMetric(row, key) {
    return Number(row.dataset[key] || 0);
  }

  function compareRows(mode) {
    if (mode === "income_desc") {
      return (a, b) => getMetric(b, "income") - getMetric(a, "income");
    }
    if (mode === "income_asc") {
      return (a, b) => getMetric(a, "income") - getMetric(b, "income");
    }
    if (mode === "properties") {
      return (a, b) => getMetric(b, "properties") - getMetric(a, "properties");
    }
    if (mode === "changes") {
      return (a, b) => getMetric(b, "changes") - getMetric(a, "changes");
    }
    return (a, b) => (a.dataset.name || "").localeCompare(b.dataset.name || "", "sk");
  }

  function applyFilters() {
    const query = search.value.trim().toLowerCase();
    const visible = rows.filter((row) => {
      const haystack = `${row.dataset.name || ""} ${row.dataset.function || ""}`;
      const matches = haystack.includes(query);
      row.hidden = !matches;
      return matches;
    });

    visible.sort(compareRows(sort.value));
    visible.forEach((row) => list.appendChild(row));
    resultCount.textContent = `${visible.length} funkcionárov`;
  }

  search.addEventListener("input", applyFilters);
  sort.addEventListener("change", applyFilters);
  applyFilters();
})();
