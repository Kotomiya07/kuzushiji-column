/**
 * 列・セグメントの注釈色を近接重複しにくい順で割り当てる。
 */
(function initAnnotationColors(root) {
    const DEFAULT_NEAR_GAP_RATIO = 0.75;

    function isFiniteBox(box) {
        return Boolean(box)
            && Number.isFinite(box.minX)
            && Number.isFinite(box.minY)
            && Number.isFinite(box.maxX)
            && Number.isFinite(box.maxY);
    }

    function boxGap(a, b) {
        const gapX = Math.max(0, Math.max(a.minX, b.minX) - Math.min(a.maxX, b.maxX));
        const gapY = Math.max(0, Math.max(a.minY, b.minY) - Math.min(a.maxY, b.maxY));
        return Math.hypot(gapX, gapY);
    }

    function boxScale(box) {
        return Math.max(1, box.maxX - box.minX, box.maxY - box.minY);
    }

    function boxWidth(box) {
        return Math.max(1, box.maxX - box.minX);
    }

    function boxHeight(box) {
        return Math.max(1, box.maxY - box.minY);
    }

    function boxesAreNear(a, b, nearGapRatio = DEFAULT_NEAR_GAP_RATIO) {
        if (!isFiniteBox(a) || !isFiniteBox(b)) return false;
        const gapX = Math.max(0, Math.max(a.minX, b.minX) - Math.min(a.maxX, b.maxX));
        const gapY = Math.max(0, Math.max(a.minY, b.minY) - Math.min(a.maxY, b.maxY));

        if (gapX === 0 && gapY === 0) return true;
        if (gapY === 0) {
            return gapX <= Math.max(boxWidth(a), boxWidth(b)) * nearGapRatio;
        }
        if (gapX === 0) {
            return gapY <= Math.max(boxHeight(a), boxHeight(b)) * nearGapRatio;
        }

        const scale = Math.max(
            Math.min(boxWidth(a), boxHeight(a)),
            Math.min(boxWidth(b), boxHeight(b)),
        );
        return boxGap(a, b) <= scale * nearGapRatio;
    }

    function circularColorDistance(a, b, paletteSize) {
        const diff = Math.abs(a - b);
        return Math.min(diff, paletteSize - diff);
    }

    function scoreCandidate(
        candidate,
        blockedColors,
        blockedFamilies,
        usedColors,
        previousColor,
        paletteSize,
        familyIds,
    ) {
        let score = 0;
        const candidateFamily = familyIds[candidate] ?? candidate;
        const previousFamily = previousColor === null ? null : (familyIds[previousColor] ?? previousColor);

        if (!blockedColors.has(candidate)) score += 1000;
        if (!blockedFamilies.has(candidateFamily)) score += 500;
        if (previousColor !== null && candidate !== previousColor) score += 100;
        if (previousFamily !== null && candidateFamily !== previousFamily) score += 80;

        let minDistance = paletteSize;
        usedColors.forEach((used) => {
            minDistance = Math.min(minDistance, circularColorDistance(candidate, used, paletteSize));
        });
        score += minDistance;
        return score;
    }

    function assignColorIndices(items, paletteSize, options = {}) {
        if (!Number.isInteger(paletteSize) || paletteSize <= 0) {
            return items.map(() => 0);
        }

        const nearGapRatio = Number.isFinite(options.nearGapRatio)
            ? options.nearGapRatio
            : DEFAULT_NEAR_GAP_RATIO;
        const familyIds = Array.isArray(options.familyIds) ? options.familyIds : [];
        const recentWindow = Number.isInteger(options.recentWindow) ? Math.max(1, options.recentWindow) : 2;
        const assignments = [];

        items.forEach((item, itemIndex) => {
            const blockedColors = new Set();
            const blockedFamilies = new Set();
            const usedColors = new Set();
            const previousColor = itemIndex > 0 ? assignments[itemIndex - 1] : null;

            const recentStart = Math.max(0, itemIndex - recentWindow);
            for (let recentIndex = recentStart; recentIndex < itemIndex; recentIndex++) {
                const recentColor = assignments[recentIndex];
                blockedColors.add(recentColor);
                blockedFamilies.add(familyIds[recentColor] ?? recentColor);
                usedColors.add(recentColor);
            }

            for (let prevIndex = 0; prevIndex < itemIndex; prevIndex++) {
                const prevColor = assignments[prevIndex];
                if (boxesAreNear(item.box, items[prevIndex].box, nearGapRatio)) {
                    blockedColors.add(prevColor);
                    blockedFamilies.add(familyIds[prevColor] ?? prevColor);
                }
                usedColors.add(prevColor);
            }

            let bestColor = 0;
            let bestScore = -Infinity;
            for (let candidate = 0; candidate < paletteSize; candidate++) {
                const score = scoreCandidate(
                    candidate,
                    blockedColors,
                    blockedFamilies,
                    usedColors,
                    previousColor,
                    paletteSize,
                    familyIds,
                );
                if (score > bestScore) {
                    bestScore = score;
                    bestColor = candidate;
                }
            }

            assignments.push(bestColor);
        });

        return assignments;
    }

    const api = {
        assignColorIndices,
        boxesAreNear,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }

    root.AnnotationColors = api;
}(typeof window !== 'undefined' ? window : globalThis));
