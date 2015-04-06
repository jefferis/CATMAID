/* -*- mode: espresso; espresso-indent-level: 2; indent-tabs-mode: nil -*- */
/* vim: set softtabstop=2 shiftwidth=2 tabstop=2 expandtab: */

(function(CATMAID) {

  "use strict";

  var ConnectivityMatrixWidget = function() {
    this.widgetID = this.registerInstance();
    this.matrix = new CATMAID.ConnectivityMatrix();
    this.rowDimension = new CATMAID.BasicSkeletonSource(this.getName() + " Rows");
    this.colDimension = new CATMAID.BasicSkeletonSource(this.getName() + " Columns");
  };

  ConnectivityMatrixWidget.prototype = {};
  $.extend(ConnectivityMatrixWidget.prototype, new InstanceRegistry());

  // Make connectivity matrix widget available in CATMAID namespace
  CATMAID.ConnectivityMatrixWidget = ConnectivityMatrixWidget;

  /* Implement interfaces */

  ConnectivityMatrixWidget.prototype.getName = function()
  {
    return "Connectivity Matrix " + this.widgetID;
  };

  /**
   * Handle destruction of widget.
   */
  ConnectivityMatrixWidget.prototype.destroy = function() {
    NeuronNameService.getInstance().unregister(this);
    this.content = null;
    this.rowDimension.destroy();
    this.colDimension.destroy();
    this.unregisterInstance();
  };

  /* Non-interface methods */

  /**
   * Create an object with all relevant information for creating a CATMAID
   * widget. All methods can expect to be executed in the context of this
   * object.
   */
  ConnectivityMatrixWidget.prototype.getWidgetConfiguration = function() {
    return {
      class: 'connectivity_matrix',
      controlsID: 'connectivity_matrix_controls' + this.widgetID,
      contentID: 'connectivity_matrix' + this.widgetID,

      /**
       * Create widget controls.
       */
      createControls: function(controls) {
        controls.appendChild(document.createTextNode('Vertical from'));
        controls.appendChild(CATMAID.skeletonListSources.createSelect(
              this.rowDimension));

        controls.appendChild(document.createTextNode('Horizontal from'));
        controls.appendChild(CATMAID.skeletonListSources.createSelect(
              this.colDimension));

        var load = document.createElement('input');
        load.setAttribute("type", "button");
        load.setAttribute("value", "Append");
        load.onclick = (function() {
          this.rowDimension.loadSource();
          this.colDimension.loadSource();
          this.refresh();
        }).bind(this);
        controls.appendChild(load);

        var clear = document.createElement('input');
        clear.setAttribute("type", "button");
        clear.setAttribute("value", "Clear");
        clear.onclick = (function() {
          if (confirm("Do you really want to clear the current selection?")) {
            this.clear();
          }
        }).bind(this);
        controls.appendChild(clear);

        var update = document.createElement('input');
        update.setAttribute("type", "button");
        update.setAttribute("value", "Refresh");
        update.onclick = this.refresh.bind(this);
        controls.appendChild(update);
      },

      /**
       * Create widget content.
       */
      createContent: function(container) {
        this.content = container;
        this.refresh();
      }
    };
  };

  /**
   * Clear all sources.
   */
  ConnectivityMatrixWidget.prototype.clear = function() {
    this.rowDimension.clear();
    this.colDimension.clear();
    this.refresh();
  };

  /**
   * Refresh the UI.
   */
  ConnectivityMatrixWidget.prototype.refresh = function(container) {
    if (!this.matrix) {
      return;
    }

    // Clrear container
    var $content = $(this.content);
    $content.empty();

    var nRows = this.rowDimension.orderedSkeletonIDs.length;
    var nCols = this.colDimension.orderedSkeletonIDs.length;

    // If there are now row or column skeletons, display a message and return
    if (0 === nRows || 0 === nCols) {
      $content.append("Please append row and column skeletons");
      return;
    }

    // Update connectivity matrix and make sure all currently looked at
    // skeletons are known to the neuron name service.
    var nns = NeuronNameService.getInstance();
    this.matrix.setRowSkeletonIDs(this.rowDimension.orderedSkeletonIDs);
    this.matrix.setColumnSkeletonIDs(this.colDimension.orderedSkeletonIDs);
    this.matrix.refresh()
      .then(nns.registerAll.bind(nns, this, this.rowDimension.getSelectedSkeletonModels()))
      .then(nns.registerAll.bind(nns, this, this.colDimension.getSelectedSkeletonModels()))
      .then((function() {
        var m = this.matrix.get();
        // Create table representation for connectivity matrix
        var table = document.createElement('table');
        table.setAttribute('class', 'partner_table');
        // Add column header, prepend one blank cell for row headers
        var colHeader = table.appendChild(document.createElement('tr'));
        colHeader.appendChild(document.createElement('th'));
        for (var c=0; c<nCols; ++c) {
          var th = document.createElement('th');
          th.appendChild(document.createTextNode(nns.getName(
                this.colDimension.orderedSkeletonIDs[c])));
          th.setAttribute('colspan', 2);
          colHeader.appendChild(th);
        }
        // Add row headers and connectivity matrix rows
        for (var r=0; r<nRows; ++r) {
          var rowSkid = this.rowDimension.orderedSkeletonIDs[r];
          var row = document.createElement('tr');
          table.appendChild(row);
          var th = document.createElement('th');
          th.appendChild(document.createTextNode(nns.getName(rowSkid)));
          row.appendChild(th);
          for (var c=0; c<nCols; ++c) {
            var colSkid = this.colDimension.orderedSkeletonIDs[c];
            var connections = m[r][c];
            var tdIn = createSynapseCountCell(rowSkid, colSkid, connections[0]);
            var tdOut = createSynapseCountCell(colSkid, rowSkid, connections[1]);
            row.appendChild(tdIn);
            row.appendChild(tdOut);
          }
        }
        $content.append(table);

        // Add a handler for openening connector selections for individual partners
        $('a[partnerID]', table).click(function () {
          var sourceID = $(this).attr('sourceID');
          var partnerID = $(this).attr('partnerID');
          if (sourceID && partnerID) {
            ConnectorSelection.show_shared_connectors(sourceID, [partnerID],
               "postsynaptic_to");
          } else {
            CATMAID.error("Could not find partner or source ID!");
          }

          return true;
        });
      }).bind(this));
  };

  /**
   * Create a synapse count table cell.
   */
  function createSynapseCountCell(sourceID, partnerID, count) {
    var td = document.createElement('td');
    td.setAttribute('class', 'syncount');
    if (count > 0) {
      // Create a links that will open a connector selection when clicked. The
      // handler to do this is created separate to only require one handler.
      var a = document.createElement('a');
      td.appendChild(a);
      a.appendChild(document.createTextNode(count));
      a.setAttribute('href', '#');
      a.setAttribute('sourceID', sourceID);
      a.setAttribute('partnerID', partnerID);
    } else {
      // Make a hidden span including the zero for semantic clarity and table exports.
      var s = document.createElement('span');
      td.appendChild(s);
      s.appendChild(document.createTextNode(count));
      s.style.display = 'none';
    }
    return td;
  }

})(CATMAID);