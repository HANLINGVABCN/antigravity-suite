/* Submit both representations so the server can reject conflicts before saving. */
((root) => {
    function syncName(link, previousName, nextName) {
        const value = String(link || '');
        if (!value.trim() || value.includes('\n')) return value;
        const hash = value.indexOf('#');
        let savedName = '';
        try { savedName = hash >= 0 ? decodeURIComponent(value.slice(hash + 1)) : ''; }
        catch { return value; }
        // Preserve a manually changed remark so conflicting entries still fail.
        if (hash >= 0 && savedName !== String(previousName || '').trim()) return value;
        const connection = hash >= 0 ? value.slice(0, hash) : value;
        return connection + '#' + encodeURIComponent(String(nextName || '').trim());
    }
    function buildUpdate(id, originalLink, link, fields) {
        const editedLink = String(link || '').trim();
        if (editedLink) return {...fields, id, link: editedLink};
        return {...fields, id};
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = {buildUpdate, syncName};
    else root.ProxyEditor = {buildUpdate, syncName};
})(typeof window !== 'undefined' ? window : globalThis);
