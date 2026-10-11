const bridgeUrl = __BRIDGE_URL__;
const bridgeToken = __BRIDGE_TOKEN__;
let pending = false;

async function activate() {
    if (pending) return;
    pending = true;
    try {
        const response = await fetch(new Request(bridgeUrl, {
            method: "POST",
            headers: {"Authorization": "Bearer " + bridgeToken},
            body: "{}"
        }));
        if (response.status !== 200) throw new Error("Bridge unavailable");
    } catch (error) {
        await fetch(new Request("http://10.0.4.20/api/display/draw", {
            method: "POST",
            body: JSON.stringify({
                application_name: "app.spotify",
                priority: 50,
                elements: [{
                    id: "connection",
                    type: "text",
                    text: "START ON MAC",
                    font: "tiny",
                    color: "#1ED760FF",
                    x: 1,
                    y: 4,
                    width: 70,
                    timeout: 3,
                    display: "front"
                }]
            })
        }));
    } finally {
        pending = false;
    }
}

activate();
setInterval(activate, 1000);
