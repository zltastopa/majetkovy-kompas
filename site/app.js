(() => {
  function setupTabs() {
    const buttons = Array.from(document.querySelectorAll(".landing-tabs button"));
    if (!buttons.length) {
      return;
    }

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".landing-tabs button").forEach((item) => {
          item.classList.remove("active");
        });
        document.querySelectorAll(".landing-section").forEach((section) => {
          section.classList.remove("active");
        });

        button.classList.add("active");
        const target = document.getElementById(`tab-${button.dataset.tab}`);
        if (target) {
          target.classList.add("active");
        }
      });
    });
  }

  const list = document.getElementById("politician-list");
  const search = document.getElementById("search");
  const sort = document.getElementById("sort");
  const resultCount = document.getElementById("result-count");

  setupTabs();

  if (!list || !search || !sort || !resultCount) {
    return;
  }

  const rows = Array.from(list.querySelectorAll(".person-row"));
  const collator = new Intl.Collator("sk", { sensitivity: "base" });
  const initialUrl = new URL(window.location.href);
  const searchHints = [
    "Robert",
    "poslanec",
    "primátor mesta",
    "štátny tajomník",
    "predseda úradu",
  ];

  function normalizeSearchText(value) {
    return (value || "")
      .toLocaleLowerCase("sk")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  const searchIndex = new Map(
    rows.map((row) => [
      row,
      normalizeSearchText(`${row.dataset.name || ""} ${row.dataset.function || ""}`),
    ]),
  );

  function setupSearchHints() {
    if (!searchHints.length) {
      return;
    }

    let index = 0;
    const renderHint = () => {
      if (search.value.trim() || document.activeElement === search) {
        return;
      }
      search.placeholder = `Skús: ${searchHints[index]}`;
      index = (index + 1) % searchHints.length;
    };

    renderHint();
    window.setInterval(renderHint, 2600);

    search.addEventListener("focus", () => {
      search.placeholder = "Hľadať meno, funkciu...";
    });

    search.addEventListener("blur", () => {
      renderHint();
    });
  }

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
    return (a, b) => collator.compare(a.dataset.name || "", b.dataset.name || "");
  }

  function syncSearchParam() {
    const rawQuery = search.value.trim();
    const nextUrl = new URL(window.location.href);
    if (rawQuery) {
      nextUrl.searchParams.set("q", rawQuery);
    } else {
      nextUrl.searchParams.delete("q");
    }

    const nextHref = `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`;
    const currentHref = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (nextHref !== currentHref) {
      window.history.replaceState({}, "", nextHref);
    }
  }

  function applyFilters() {
    const query = normalizeSearchText(search.value.trim());
    const visible = rows.filter((row) => {
      const haystack = searchIndex.get(row) || "";
      const matches = haystack.includes(query);
      return matches;
    });

    visible.sort(compareRows(sort.value));
    rows.forEach((row) => {
      row.hidden = true;
    });

    const shown = visible.slice(0, 200);
    shown.forEach((row) => {
      row.hidden = false;
      list.appendChild(row);
    });
    resultCount.textContent = `${visible.length} funkcionárov`;

    const existingNote = list.querySelector("[data-overflow-note]");
    if (existingNote) {
      existingNote.remove();
    }

    if (visible.length > 200) {
      const note = document.createElement("li");
      note.dataset.overflowNote = "true";
      note.className = "list-more";
      note.textContent = `...a ${visible.length - 200} ďalších — upresnite hľadanie`;
      list.appendChild(note);
    }

    syncSearchParam();
  }

  const initialQuery = initialUrl.searchParams.get("q");
  if (initialQuery) {
    search.value = initialQuery;
  }

  search.addEventListener("input", applyFilters);
  sort.addEventListener("change", applyFilters);
  setupSearchHints();
  applyFilters();
})();
