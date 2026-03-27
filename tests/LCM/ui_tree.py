HTML_PART = """
        <div class="column" style="background: #010409;">
            <div class="column-header">DAG HERITAGE MAP</div>
            <div id="dag-viewport">
                <div id="dag-container">
                    <svg id="dag-svg"></svg>
                    <div class="tree" id="dag-tree"></div>
                </div>
            </div>
        </div>
"""

JS_LOGIC = """
// Logic vẽ đường cong cực mượt
function drawCurves() {
    const svg = document.getElementById('dag-svg');
    const tree = document.getElementById('dag-tree');
    if(!svg || !tree) return;
    
    svg.innerHTML = ''; 
    const lis = tree.querySelectorAll('li');
    // Lấy bounding client của container (vùng chứa nội tại) thay vì viewport để đường vẽ ăn khớp khi kéo thả
    const svgRect = svg.getBoundingClientRect();

    lis.forEach(li => {
        const ul = li.querySelector(':scope > ul');
        if (!ul) return;
        const parentNode = li.querySelector(':scope > .node');
        const childNodes = Array.from(ul.children).map(c => c.querySelector(':scope > .node')).filter(n => n);
        if (!parentNode) return;
        const pRect = parentNode.getBoundingClientRect();

        childNodes.forEach(childNode => {
            const cRect = childNode.getBoundingClientRect();
            // Tính toán bù trừ Zoom/Pan
            const startX = pRect.left + pRect.width / 2 - svgRect.left;
            const startY = pRect.bottom - svgRect.top;
            const endX = cRect.left + cRect.width / 2 - svgRect.left;
            const endY = cRect.top - svgRect.top;
            const curveY = startY + (endY - startY) / 2;
            
            const d = 'M ' + startX + ' ' + startY + ' C ' + startX + ' ' + curveY + ', ' + endX + ' ' + curveY + ', ' + endX + ' ' + endY;
            
            const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute("d", d);
            path.setAttribute("fill", "none");
            path.setAttribute("stroke-width", "1.5"); // Làm nét dày lên 1 xíu

            if (childNode.classList.contains('node-raw')) {
                path.setAttribute("stroke", "#484f58");
                path.setAttribute("stroke-dasharray", "4 4");
            } else if (childNode.classList.contains('node-d0')) {
                path.setAttribute("stroke", "var(--color-d0)");
                path.setAttribute("opacity", "0.6");
            } else if (childNode.classList.contains('node-d1')) {
                path.setAttribute("stroke", "var(--color-d1)");
            } else if (childNode.classList.contains('node-d2')) {
                path.setAttribute("stroke", "var(--color-d2)");
            } else {
                path.setAttribute("stroke", "#f0f6fc");
            }
            svg.appendChild(path);
        });
    });
}

// Logic Pan (Kéo thả) cho DAG Map
const viewport = document.getElementById('dag-viewport');
const container = document.getElementById('dag-container');
let isPanning = false, startX, startY, scrollLeft, scrollTop;

if(viewport) {
    viewport.addEventListener('mousedown', (e) => {
        isPanning = true;
        startX = e.pageX - viewport.offsetLeft;
        startY = e.pageY - viewport.offsetTop;
        scrollLeft = viewport.scrollLeft;
        scrollTop = viewport.scrollTop;
    });
    viewport.addEventListener('mouseleave', () => { isPanning = false; });
    viewport.addEventListener('mouseup', () => { isPanning = false; });
    viewport.addEventListener('mousemove', (e) => {
        if (!isPanning) return;
        e.preventDefault();
        const x = e.pageX - viewport.offsetLeft;
        const y = e.pageY - viewport.offsetTop;
        viewport.scrollLeft = scrollLeft - (x - startX);
        viewport.scrollTop = scrollTop - (y - startY);
    });
}
"""

JS_RENDER = """
if(data.dag_tree && data.dag_tree.length > 0) {
    const nodesMap = {};
    const allNodes = data.dag_tree.flat();
    
    allNodes.forEach(n => {
        let parsedParents = [];
        if (n.parent_ids) {
            try {
                parsedParents = typeof n.parent_ids === 'string' ? JSON.parse(n.parent_ids) : n.parent_ids;
            } catch(e) { parsedParents = []; }
        }
        nodesMap[n.id] = { ...n, parent_ids: parsedParents, uiChildren: [], hasUiParent: false };
    });
    
    allNodes.forEach(n => {
        const currentNode = nodesMap[n.id];
        if (currentNode.parent_ids && currentNode.parent_ids.length > 0) {
            currentNode.parent_ids.forEach(p_id => {
                if (nodesMap[p_id]) {
                    const uiParent = nodesMap[p_id];
                    if (!uiParent.uiChildren.find(c => c.id === currentNode.id)) {
                        uiParent.uiChildren.push(currentNode);
                        currentNode.hasUiParent = true;
                    }
                }
            });
        }
    });
    
    const uiRoots = Object.values(nodesMap).filter(n => !n.hasUiParent);

    function renderNode(node) {
        const d = node.depth;
        const nodeClass = 'node-d' + Math.min(d, 2);
        const tagClass = 'tag-d' + Math.min(d, 2);
        
        let childContent = "";
        if (node.uiChildren.length > 0) {
            childContent = '<ul>' + node.uiChildren.map(renderNode).join('') + '</ul>';
        } else if (d === 0) {
            childContent = '<ul><li><div class="node node-raw"><div class="tag tag-raw">RAW MSGS</div><div class="meta">Verbatim Store</div></div></li></ul>';
        }

        const desc = d === 0 ? 'Cluster' : (node.descendant_count || 0) + ' desc';
        
        return '<li>' +
               '<div class="node ' + nodeClass + '" id="node-' + node.id + '">' +
                   '<div class="tag ' + tagClass + '">SUMMARY • D' + d + '</div>' +
                   '<div class="n-val">' + node.id + '</div>' +
                   '<div class="meta">' + node.token_count + ' tok | ' + desc + '</div>' +
               '</div>' + childContent +
               '</li>';
    }

    document.getElementById('dag-tree').innerHTML = '<ul>' + uiRoots.map(renderNode).join('') + '</ul>';
    
    requestAnimationFrame(() => {
        if (typeof drawCurves === 'function') drawCurves();
    });
} else {
    document.getElementById('dag-tree').innerHTML = '';
    const svg = document.getElementById('dag-svg');
    if(svg) svg.innerHTML = '';
}
"""