import re
with open('dashboard/index.html', 'r') as f:
    content = f.read()

replacement = """
    function startLogStream() {
        const container = document.getElementById('log-container');
        container.innerHTML = '';
        
        // Fetch recent first
        api('/api/logs').then(data => {
            if (data.logs) {
                data.logs.forEach(l => appendLog(l));
                container.scrollTop = container.scrollHeight;
            }
        });

        if (eventSource) eventSource.close();
        eventSource = new EventSource('/api/logs/stream?token=' + encodeURIComponent(token));
        eventSource.onmessage = (e) => {
            appendLog(e.data);
            const container = document.getElementById('log-container');
            container.scrollTop = container.scrollHeight;
        };
        eventSource.onerror = () => {
            console.error('SSE Error');
            eventSource.close();
        };
    }
"""

content = re.sub(r"    function startLogStream\(\).*?    function appendLog\(line\)", replacement.strip() + "\n\n    function appendLog(line)", content, flags=re.DOTALL)

with open('dashboard/index.html', 'w') as f:
    f.write(content)
