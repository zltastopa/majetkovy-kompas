(() => {
  const collator = new Intl.Collator("sk", { sensitivity: "base" });

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

  function setupLatestChangesSort() {
    const sorts = Array.from(document.querySelectorAll("[data-latest-changes-sort]"));
    if (!sorts.length) {
      return;
    }

    const compareCards = (mode) => {
      if (mode === "updated_asc") {
        return (a, b) => (a.dataset.lastUpdated || "").localeCompare(b.dataset.lastUpdated || "");
      }
      if (mode === "changes_desc") {
        return (a, b) => Number(b.dataset.changeCount || 0) - Number(a.dataset.changeCount || 0);
      }
      if (mode === "name") {
        return (a, b) => collator.compare(a.dataset.name || "", b.dataset.name || "");
      }
      return (a, b) => (b.dataset.lastUpdated || "").localeCompare(a.dataset.lastUpdated || "");
    };

    sorts.forEach((sortEl) => {
      const list = sortEl.closest(".landing-section, body")?.querySelector(".highlight-list[id^='latest-changes-list-']");
      if (!list) {
        return;
      }

      const count = sortEl.closest(".landing-section, body")?.querySelector(".result-count[id^='latest-changes-count-']");
      const cards = Array.from(list.querySelectorAll(".highlight-card--change"));
      if (!cards.length) {
        return;
      }

      const render = () => {
        cards.sort(compareCards(sortEl.value));
        cards.forEach((card) => {
          list.appendChild(card);
        });
        if (count) {
          count.textContent = `${cards.length} záznamov`;
        }
      };

      sortEl.addEventListener("change", render);
      render();
    });
  }

  const list = document.getElementById("politician-list");
  const search = document.getElementById("search");
  const sort = document.getElementById("sort");
  const resultCount = document.getElementById("result-count");

  setupTabs();
  setupLatestChangesSort();

  if (!list || !search || !sort || !resultCount) {
    return;
  }

  const rows = Array.from(list.querySelectorAll(".person-row"));
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
