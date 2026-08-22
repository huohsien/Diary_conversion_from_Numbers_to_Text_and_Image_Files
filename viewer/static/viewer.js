
(function () {
    "use strict";

    function px(value) {
        const n = parseFloat(value);
        return Number.isFinite(n) ? n : 0;
    }

    function layoutRows() {
        const table = document.querySelector("table");
        if (!table) return;

        const tableStyle = getComputedStyle(table);
        const basicWidth = px(tableStyle.getPropertyValue("--basic-width")) || 120;

        document.querySelectorAll("tr.diary-row").forEach((row) => {
            const textBlocks = Array.from(row.querySelectorAll(".text-value"));
            const imageFits = Array.from(row.querySelectorAll(".image-fit"));

            /*
             * Numbers-like row-height rule:
             *
             * 1. If the record has text, wrapped TEXT determines the row height.
             *    Images must fit inside that height and must NOT make the row taller.
             *
             * 2. If the record has no text and only images, fall back to one basic
             *    square-cell height as a sensible inspection height.
             *
             * This is the key behavior for rows such as 06:49: the purple text block
             * should end almost exactly at the bottom of the row, with no artificial
             * square-image baseline adding whitespace underneath it.
             */
            let targetHeight = 0;

            textBlocks.forEach((block) => {
                const cell = block.closest("td");
                if (!cell) return;

                const cellStyle = getComputedStyle(cell);
                const verticalPadding =
                    px(cellStyle.paddingTop) +
                    px(cellStyle.paddingBottom);

                const needed = block.scrollHeight + verticalPadding;
                targetHeight = Math.max(targetHeight, needed);
            });

            if (textBlocks.length === 0 && imageFits.length > 0) {
                targetHeight = basicWidth;
            }

            const timeValue = row.querySelector(".time-value");
            if (timeValue) {
                targetHeight = Math.max(targetHeight, timeValue.scrollHeight + 8);
            }

            // Blank source rows remain compact, like Numbers.
            if (targetHeight <= 0) {
                targetHeight = 28;
            }

            row.style.height = `${Math.ceil(targetHeight)}px`;

            imageFits.forEach((fit) => {
                fit.style.height = `${Math.ceil(targetHeight)}px`;
            });
        });
    }

    function scheduleLayout() {
        requestAnimationFrame(() => {
            layoutRows();
            requestAnimationFrame(layoutRows);
        });
    }

    window.addEventListener("load", scheduleLayout);
    window.addEventListener("resize", scheduleLayout);

    document.querySelectorAll(".image-fit img").forEach((img) => {
        if (!img.complete) {
            img.addEventListener("load", scheduleLayout, { once: true });
        }
    });
})();
