/* Submit both representations so the server can reject conflicts before saving. */
((root) => {
    function buildUpdate(id, originalLink, link, fields) {
        const editedLink = String(link || '').trim();
        if (editedLink) return {...fields, id, link: editedLink};
        return {...fields, id};
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = {buildUpdate};
    else root.ProxyEditor = {buildUpdate};
})(typeof window !== 'undefined' ? window : globalThis);
