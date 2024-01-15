<html>
    <head>
        <title>Apartments</title>
        <script src="https://code.jquery.com/jquery-3.3.1.min.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery.tablesorter/2.31.1/js/jquery.tablesorter.min.js"></script>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-EVSTQN3/azprG1Anm3QDgpJLIm9Nao0Yz1ztcQTwFspd3yD65VohhpuuCOmLASjC" crossorigin="anonymous">
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/js/bootstrap.bundle.min.js" integrity="sha384-MrcW6ZMFYlzcLA8Nl+NtUVF0sA7MsXsP1UyJoMp4YLEuNSfAP+JcXn/tWtIaxVXM" crossorigin="anonymous"></script>

        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.3/dist/leaflet.css"
            integrity="sha256-kLaT2GOSpHechhsozzB+flnD+zUyjE2LlfWPgU04xyI="
            crossorigin=""/>
        <!-- Make sure you put this AFTER Leaflet's CSS -->
        <script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"
            integrity="sha256-WBkoXOwTeyKclOHuWtc+i2uENFpDZ9YPdf5Hf+D7ewM="
            crossorigin=""></script>

        <style>
            #map {
                height: 500px;
                width: 100%;
                position: sticky;
                top: 0;
            }

            thead {
                position: sticky;
                background-color: white;
            }
            tbody:before {
                line-height:2em; 
                content:"\200C"; 
                display:block;
            }
            tr {
              --bs-table-hover-bg: #cfe2ff;
            }
        </style>

    </head>
    <body>

        <div id="map"></div>

        <div class="table-responsive">
        {{ table }}
        </div>

        <script>
            $(document).ready(function() {
                $(".sortable").tablesorter();
            });

            // show the geojson properties in a popup
            function onEachFeature(feature, layer) {
                if (feature.properties) {
                    // get the aptID from the text in the a tag
                    var aptID = feature.properties['translateUrl'].match(/(\d+)/)[0];
                    // loop through the key value pairs and add them to the popup
                    var popupContent = "<dl class='row' data-apt-id='" + aptID + "'>";
                    for (var key in feature.properties) {
                        let value = feature.properties[key];
                        popupContent += "<dt class='col-6'>" + key + "</dt><dd class='col-6'>" + value + "</dd>";
                    }
                    popupContent += "</dl>";

                    layer.bindPopup(popupContent, {maxWidth: 600, minWidth: 300});

                    // find the nearest tr element to the a tag with the data-apt-id and add a click event to it
                    // to open the popup
                    var tr = $("a[data-apt-id='" + aptID + "']").closest('tr');
                    tr.click(function() {
                        // close all other popups
                        map.closePopup();
                        layer.openPopup();
                    });
                    // change the marker color when the tr is hovered
                    tr.hover(function() {
                        layer.setIcon(L.icon({
                            iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
                            shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
                            iconSize: [25, 41],
                            iconAnchor: [12, 41],
                            popupAnchor: [1, -34],
                            shadowSize: [41, 41]
                        }));
                    }, function() {
                        layer.setIcon(L.icon({
                            iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-blue.png',
                            shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
                            iconSize: [25, 41],
                            iconAnchor: [12, 41],
                            popupAnchor: [1, -34],
                            shadowSize: [41, 41]
                        }));
                    });

                }
            }



            var map = L.map('map').setView([{{location_y}}, {{location_x}}], 13);

            L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '&copy; <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            }).addTo(map);

            var data = {{ data|safe }};
            L.geoJSON(data, {onEachFeature: onEachFeature}).addTo(map);

            // add a hide button to each table row
            $("tr").each(function() {
                var tr = $(this);
                var td = tr.find("td:last");
                var button = $("<button class='btn btn-sm btn-outline-secondary'>Hide</button>");
                button.click(function() {
                    tr.hide();
                });
                td.append(button);
            });
        </script>
    </body>
</html>
