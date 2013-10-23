/* -*- mode: espresso; espresso-indent-level: 2; indent-tabs-mode: nil -*- */
/* vim: set softtabstop=2 shiftwidth=2 tabstop=2 expandtab: */

"use strict";

var SelectionTable = function() {
  this.widgetID = this.registerInstance();
  this.registerSource();

  this.skeletons = [];
  this.skeleton_ids = {}; // skeleton_id vs index in skeleton array
  this.togglevisibleall = false;
  this.selected_skeleton_id = null;
  this.highlighting_color = "#d6ffb5";
  this.next_color_index = 0;
  this.gui = new this.GUI(this, 20);
};

SelectionTable.prototype = {};
$.extend(SelectionTable.prototype, new InstanceRegistry());
$.extend(SelectionTable.prototype, new SkeletonSource());

SelectionTable.prototype.getName = function() {
  return "Selection " + this.widgetID;
};

SelectionTable.prototype.destroy = function() {
  this.clear();
  this.unregisterInstance();
  this.unregisterSource();
};

SelectionTable.prototype.updateModel = function(model, source_chain) {
  if (source_chain && (this in source_chain)) return; // break propagation loop
  if (!source_chain) source_chain = {};
  source_chain[this] = this;

  if (!(model.id in this.skeleton_ids)) {
    growlAlert("Oops", this.getName() + " does not have skeleton #" + model.id);
    return;
  }
  this.skeletons[this.skeleton_ids[model.id]] = model.clone();
  this.gui.update();
  this.notifyLink(model, source_chain);
};

SelectionTable.prototype.SkeletonModel = function( id, neuronname, color ) {
    this.id = parseInt(id);
    this.baseName = neuronname;
    this.selected = true;
    this.pre_visible = true;
    this.post_visible = true;
    this.text_visible = false;
    this.color = color;
};

SelectionTable.prototype.SkeletonModel.prototype = {};

SelectionTable.prototype.SkeletonModel.prototype.clone = function() {
  var m = new SelectionTable.prototype.SkeletonModel(this.id, this.neuronname, this.color.clone());
  m.selected = this.selected;
  m.pre_visible = this.pre_visible;
  m.post_visible = this.post_visible;
  m.text_visible = this.text_visible;
  return m;
};

// TODO doesn't do anything?
SelectionTable.prototype.SkeletonModel.prototype.property_dialog = function() {
  var dialog = document.createElement('div');
  dialog.setAttribute("id", "dialog-confirm");
  dialog.setAttribute("title", "Skeleton Properties");

  var entry = document.createElement('input');
  entry.setAttribute("type", "text");
  entry.setAttribute("id", "skeleton-selected");
  entry.setAttribute("value", self.selected );
  dialog.appendChild(entry);

  $(dialog).dialog({
    height: 440,
    modal: true,
    buttons: {
      "Cancel": function() {
        $(this).dialog("close");
      },
      "OK": function() {
        $(this).dialog("close");
      }
    }
  });
};

SelectionTable.prototype.SkeletonModel.prototype.skeleton_info = function() {
  // TODO if the skeleton is loaded in the WebGLApp, then all of this information
  // is already present in the client
  // Additionally, the node count should be continued by the user contribution
  // (that is, how many nodes each user contributed). Same for review status.
  // And the "Downstream skeletons" should be split into two: skeletons with more than one node, and skeletons with one single node (placeholder pre- or postsynaptic nodes).
  requestQueue.register(django_url + project.id + '/skeleton/' + this.id + '/statistics', "POST", {},
      function (status, text, xml) {
        if (status === 200) {
          if (text && text !== " ") {
            var e = $.parseJSON(text);
            if (e.error) {
                alert(e.error);
            } else {
              var dialog = document.createElement('div');
              dialog.setAttribute("id", "dialog-confirm");
              dialog.setAttribute("title", "Skeleton Information");
              var msg = document.createElement('p');
              msg.innerHTML = 
                  "Neuron Name: " + self.baseName + ' #' + self.id + "<br />" +
                  "Node count: " + e.node_count + "<br />" +
                  "Postsynaptic sites: " + e.postsynaptic_sites + "<br />" +
                  "Upstream skeletons: " + e.input_count + "<br />" +
                  "Presynaptic sites: " + e.presynaptic_sites + "<br />" +
                  "Downstream skeletons: " + e.output_count + "<br />" +
                  "Cable length: " + e.cable_length + " nm <br />" +
                  "Construction time: " + e.measure_construction_time + "<br />" +
                  "Reviewed: " + e.percentage_reviewed + " %<br />";
              dialog.appendChild(msg);

              $(dialog).dialog({
                height: 440,
                modal: true,
                buttons: {
                  "OK": function() {
                    $(this).dialog("close");
                  }
                }
              });
            }
          }
        }
      });
};


SelectionTable.prototype.COLORS = [[1, 1, 0], // yellow
                                   [1, 0, 1], // magenta
                                   [0.5, 0.5, 1], // light blue
                                   [1, 0, 0], // red
                                   [1, 1, 1], // white
                                   [0, 1, 0], // green
                                   [0, 1, 1], // cyan
                                   [1, 0.5, 0], // orange
                                   [0, 0, 1], // blue
                                   [0.75, 0.75, 0.75], // silver
                                   [1, 0.5, 0.5], // pinkish
                                   [0.5, 1, 0.5], // light cyan
                                   [0.5, 1, 0], // light green
                                   [0, 1, 0.5], // pale green
                                   [1, 0, 0.5], // purplish
                                   [0.5, 0, 0], // maroon
                                   [0.5, 0.5, 0.5], // grey
                                   [0.5, 0, 0.5], // purple
                                   [0, 0, 0.5], // navy blue
                                   [1, 0.38, 0.28], // tomato
                                   [0.85, 0.64, 0.12], // gold
                                   [0.25, 0.88, 0.82], // turquoise
                                   [1, 0.75, 0.79]]; // pink


SelectionTable.prototype.pickColor = function() {
  var c = this.COLORS[this.next_color_index % this.COLORS.length];
  var color = new THREE.Color().setRGB(c[0], c[1], c[2]);
  if (this.next_color_index < this.COLORS.length) {
    this.next_color_index += 1;
    return color;
  }
  // Else, play a variation on the color's hue (+/- 0.25) and saturation (from 0.5 to 1)
  var hsl = color.getHSL();
  color.setHSL((hsl.h + (Math.random() - 0.5) / 2.0) % 1.0,
               Math.max(0.5, Math.min(1.0, (hsl.s + (Math.random() - 0.5) * 0.3))),
               hsl.l);
  this.next_color_index += 1;
  return color;
};

SelectionTable.prototype.highlight = function( skeleton_id ) {
  if (this.selected_skeleton_id in this.skeleton_ids) {
    $('#skeletonrow' + this.widgetID + '-' + this.selected_skeleton_id).css('background-color', 'white');
    this.selected_skeleton_id = null;
  }
  if (skeleton_id in this.skeleton_ids) {
    $('#skeletonrow'+ this.widgetID + '-' + skeleton_id).css('background-color', this.highlighting_color);
    this.selected_skeleton_id = skeleton_id;
  }
};

/** Static access to the first selection table found. */
SelectionTable.prototype.getOrCreate = function() {
  var selection = SelectionTable.prototype.getFirstInstance();
  if (!selection) WindowMaker.create('neuron-staging-area');
  return SelectionTable.prototype.getFirstInstance();
};

SelectionTable.prototype.toggleSelectAllSkeletons = function() {
  this.skeletons.forEach(function(skeleton) {
    this.selectSkeleton(skeleton, this.togglevisibleall);
  });
  this.togglevisibleall = !this.togglevisibleall;
};

/** setup button handlers */
SelectionTable.prototype.init = function() {
  // Set the default source at the 'Active skeleton'
  var select = $('#' + SkeletonListSources.createSelectID(this))[0];
  for (var i=0; i<select.options.length; ++i) {
    if ('Active skeleton' === select.options[i].value) {
      select.selectedIndex = i;
      break;
    }
  }
  // Load the default source (should at least be the 'Active skeleton')
  this.loadSource();

  var clear = this.clear.bind(this),
      toggleSelectAllSkeletons = this.toggleSelectAllSkeletons.bind(this);

  $('#webgl-rmall').click(function() {
    if (confirm("Empty selection table?")) {
      clear();
    }
  });

  $('#webgl-show').click(toggleSelectAllSkeletons);

  // TODO add similar buttons and handlers for pre and post
};

/** sks: object with skeleton_id as keys and neuron names as values. */
SelectionTable.prototype.insertSkeletons = function(sks, callback) {
  var models = {};
  Object.keys(sks).forEach(function(id) {
    models[id] = new this.SkeletonModel(id, sks[id], this.pickColor());
  }, this);
  this.append(models);

  this.gui.update();

  if (callback) callback();
};

SelectionTable.prototype.addSkeletons = function(ids, callback) {
  var skeleton_ids = this.skeleton_ids;
  ids = ids.reduce(function(a, skid) {
    if (!(skid in skeleton_ids)) a.push(parseInt(skid));
    return a;
  }, []);
  var self = this;
  requestQueue.register(django_url + project.id + '/skeleton/neuronnames', 'POST',
    {skids: ids},
    function(status, text) {
      if (200 !== status) return;
      var json = $.parseJSON(text);
      if (json.error) { alert(json.error); return; }
      self.insertSkeletons(json, callback);
    });
};

SelectionTable.prototype.append = function(models) {
  var skeleton_ids = Object.keys(models);
  if (0 === skeleton_ids.length) {
    growlAlert("Info", "No skeletons selected!"); // at source
    return;
  }
  skeleton_ids.forEach(function(skeleton_id) {
    if (skeleton_id in this.skeleton_ids) {
      // Update skeleton
      this.skeletons[this.skeleton_ids[skeleton_id]] = models[skeleton_id];
      return;
    }
    this.skeletons.push(models[skeleton_id]);
    this.skeleton_ids[skeleton_id] = this.skeletons.length -1;
  }, this);

  this.gui.update();

  if (this.linkTarget) {
    // Prevent propagation loop by checking if the target already has all the skeletons
    var diff = SkeletonListSources.findDifference(this.linkTarget, models);
    if (Object.keys(diff).length > 0) {
      this.linkTarget.append(diff);
    }
  }
};

/** ids: an array of Skeleton IDs. */
SelectionTable.prototype.removeSkeletons = function(ids) {
  if (1 === ids.length) {
    if (ids[0] in this.skeleton_ids) {
      // Remove element
      this.skeletons.splice(this.skeleton_ids[ids[0]], 1);
      // Edit selection
      if (ids[0] === this.selected_skeleton_id) {
        this.selected_skeleton_id = null;
      }
    }
  } else {
    var ids_set = ids.reduce(function(o, id) { o[id] = null; return o; }, {});
    // Recreate skeletons array
    this.skeletons = this.skeletons.filter(function(sk) {
      return !(sk.id in ids_set);
    });
    // Edit selection
    if (this.selected_skeleton_id in ids_set) {
      this.selected_skeleton_id = null;
    }
  }

  // Recreate map of indices
  this.skeleton_ids = this.skeletons.reduce(function(o, sk, i) {
    o[sk.id] = i;
    return o;
  }, {});

  this.gui.update();

  if (this.linkTarget) {
    // Prevent propagation loop by checking if the target has the skeletons anymore
    if (ids.some(this.linkTarget.hasSkeleton, this.linkTarget)) {
      this.linkTarget.removeSkeletons(ids);
    }
  }
};

SelectionTable.prototype.clear = function() {
  this.skeletons = [];
  this.skeleton_ids = {};
  this.gui.clear();
  this.selected_skeleton_id = null;
  this.next_color_index = 0;
};
 
/** Set the color of all skeletons based on the state of the "Color" pulldown menu. */
SelectionTable.prototype.set_skeletons_base_color = function() {
  // TODO just convert menu into a button to randomize color
  var skeletonsColorMethod = $('#skeletons_base_color' + this.widgetID + ' :selected').attr("value");
  if ("random" === skeletonsColorMethod) {
    this.next_color_index = 0; // reset
    var colors = this.skeletons.map(function(skeleton) {
      skeleton.color = this.pickColor();
      this.gui.update_skeleton_color_button(skeleton);
      return skeleton.color;
    }, this);
  }

  if (this.linkTarget) {
    var models = getSelectedSkeletonModels();
    // Prevent propagation loop by checking if the target already has all the skeletons
    var diff = SkeletonListSources.findDifference(this.linkTarget, models);
    if (Object.keys(diff).length > 0) {
      this.linkTarget.append(diff);
    }
  }
};
 
//SelectionTable.prototype.update_skeleton_color_in_3d = function( skeleton ) {
//  if( $('#view_in_3d_webgl_widget').length && WebGLApp.has_skeleton( skeleton.id ) ) {
//    WebGLApp.changeSkeletonColors( [skeleton.id], [skeleton.color] );
//  }
//};

SelectionTable.prototype.getSkeletonModel = function( id ) {
  if (id in this.skeleton_ids) {
    return this.skeletons[this.skeleton_ids[id]].clone();
  }
};

SelectionTable.prototype.getSelectedSkeletonModels = function() {
  return this.skeletons.reduce(function(o, model) {
    if (model.selected) {
      o[model.id] = model.clone();
    }
    return o;
  }, {});
};

SelectionTable.prototype.getSkeletonModels = function() {
  return this.skeletons.reduce(function(o, model) {
      o[model.id] = model.clone();
    return o;
  }, {});
};

/** Update neuron names and remove stale non-existing skeletons while preserving
 *  ordering and properties of each skeleton currently in the selection. */
SelectionTable.prototype.update = function() {
  var models = this.getSkeletonModels();
  var indices = this.skeleton_ids;
  this.clear();
  var self = this;
  requestQueue.register(django_url + project.id + '/skeleton/neuronnames', 'POST',
    {skids: Object.keys(models)},
    function(status, text) {
      if (200 !== status) return;
      var json = $.parseJSON(text);
      var o = {};
      Object.keys(json).forEach(function(skid) {
        o[indices[skid]] = skid;
      });
      Object.keys(o).map(Number).sort().forEach(function(index) {
        var skid = o[index];
        self.skeletons.push(models[skid]);
        self.skeleton_ids[skid] = self.skeletons.length -1;
      });
      self.gui.update();
    });
};

SelectionTable.prototype.getSkeletonColor = function( id ) {
  var sk = this.getSkeletonModel(id);
  if (sk) return sk.color.clone();
};

SelectionTable.prototype.getSelectedSkeletons = function() {
  return this.skeletons.reduce(function(a, skeleton) {
    if (skeleton.selected) a.push(skeleton.id);
    return a;
  }, []);
};

SelectionTable.prototype.hasSkeleton = function(skeleton_id) {
  return skeleton_id in this.skeleton_ids;
};

SelectionTable.prototype.getSelectedSkeletonNames = function() {
  return this.skeletons.reduce(function(o, skeleton) {
    if (skeleton.selected) o[skeleton.id] = skeleton.baseName;
    return o;
  }, {});
};

SelectionTable.prototype.setVisible = function(skeleton_ids, visible) {
  skeleton_ids.forEach(function(skid) {
    if (skid in this.skeleton_ids) {
      this.skeletons[this.skeleton_ids[skid]].selected = visible;
    }
  }, this);
  this.gui.update();
};

SelectionTable.prototype.get_all_skeletons = function() {
  return Object.keys( skeleton_ids );
};

SelectionTable.prototype.showPrevious = function() {
  this.gui.showPrevious();
};

SelectionTable.prototype.showNext = function() {
  this.gui.showNext();
};


SelectionTable.prototype.GUI = function(table, max) {
  this.table = table;
  this.first = 0;
  this.max = max;
};

SelectionTable.prototype.GUI.prototype = {};

SelectionTable.prototype.GUI.prototype.clear = function() {
  this.first = 0;
  this.update();
};

SelectionTable.prototype.GUI.prototype.showPrevious = function() {
  if (0 === this.first) return;
  this.first -= this.max;
  this.update();
};

SelectionTable.prototype.GUI.prototype.showNext = function() {
  if (this.first + this.max > this.table.skeletons.length) return;
  this.first += this.max;
  this.update();
};

SelectionTable.prototype.GUI.prototype.update_skeleton_color_button = function(skeleton) {
  $('#skeletonaction-changecolor-' + this.table.widgetID + '-' + skeleton.id).css("background-color", '#' + skeleton.color.getHexString());
};

/** Remove all, and repopulate with the current range. */
SelectionTable.prototype.GUI.prototype.update = function() {
  // Cope with changes in size
  if (this.first >= this.table.skeletons.length) {
    this.first = Math.max(0, this.table.skeletons.length - this.max);
  }

  // Update GUI state
  var widgetID = this.table.widgetID;
  var one = 0 === this.table.skeletons.length? 0 : 1;
  $('#selection_table_first' + widgetID).text(this.first + one);
  $('#selection_table_last' + widgetID).text(Math.min(this.first + this.max + one, this.table.skeletons.length));
  $('#selection_table_length' + widgetID).text(this.table.skeletons.length);

  // Remove all table rows
  $("tr[id^='skeletonrow" + widgetID + "']").remove();
  // Re-add the range
  this.table.skeletons.slice(this.first, this.first + this.max).forEach(this.append, this);

  // If the active skeleton is within the range, highlight it
  this.selected_skeleton_id = SkeletonAnnotations.getActiveSkeletonId();
  if (this.selected_skeleton_id) this.table.highlight(this.selected_skeleton_id);
};

SelectionTable.prototype.GUI.prototype.append = function (skeleton) {
  var table = this.table,
      widgetID = this.table.widgetID;

  var rowElement = $('<tr/>').attr({
    id: 'skeletonrow' + widgetID + '-' + skeleton.id
  });

  var td = $(document.createElement("td"));
  td.append( $(document.createElement("img")).attr({
    value: 'Nearest node'
  })
    .click( function( event )
    {
      TracingTool.goToNearestInNeuronOrSkeleton( 'skeleton', skeleton.id );
    })
    .attr('src', STATIC_URL_JS + 'widgets/themes/kde/activate.gif')
  );
  td.append( $(document.createElement("img")).attr({
        value: 'Remove'
        })
        .click( function( event )
        {
          table.removeSkeletons( [skeleton.id] );
        })
        .attr('src', STATIC_URL_JS + 'widgets/themes/kde/delete.png')
        .text('Remove!')
  );
  rowElement.append( td );

  rowElement.append(
    $(document.createElement("td")).text( skeleton.baseName + ' #' + skeleton.id )
  );

  // show skeleton
  rowElement.append(
    $(document.createElement("td")).append(
      $(document.createElement("input")).attr({
                id:    'skeletonshow' + widgetID + '-' + skeleton.id,
                //name:  skeleton.baseName,
                value: skeleton.id,
                type:  'checkbox',
                checked: skeleton.selected
        })
        .click( function( event )
        {
          var vis = $('#skeletonshow' + widgetID + '-' + skeleton.id).is(':checked')
          skeleton.selected = vis;
          $('#skeletonpre' + widgetID + '-' + skeleton.id).attr({checked: vis});
          skeleton.pre_visible = vis;
          $('#skeletonpost' + widgetID + '-' + skeleton.id).attr({checked: vis});
          skeleton.post_visible = vis;
          if (!vis) {
            // hide text
            $('#skeletontext' + widgetID + '-' + skeleton.id).attr({checked: vis});
            skeleton.text_visible = vis;
          }
          table.notifyLink(skeleton);
        } )
  ));

  // show pre
  rowElement.append(
    $(document.createElement("td")).append(
      $(document.createElement("input")).attr({
                id:    'skeletonpre' + widgetID + '-' + skeleton.id,
                //name:  skeleton.baseName,
                value: skeleton.id,
                type:  'checkbox',
                checked:true
        })
        .click( function( event )
        {
          skeleton.pre_visible = $('#skeletonpre' + widgetID + '-' + skeleton.id).is(':checked');
          table.notifyLink(skeleton);
        } )
  ));

  // show post
  rowElement.append(
    $(document.createElement("td")).append(
      $(document.createElement("input")).attr({
                id:    'skeletonpost' + widgetID + '-' + skeleton.id,
                //name:  skeleton.baseName,
                value: skeleton.id,
                type:  'checkbox',
                checked:true
        })
        .click( function( event )
        {
          skeleton.post_visible = $('#skeletonpost' + widgetID + '-' + skeleton.id).is(':checked');
          table.notifyLink(skeleton);
        } )
  ));

  rowElement.append(
    $(document.createElement("td")).append(
      $(document.createElement("input")).attr({
                id:    'skeletontext' + widgetID + '-' + skeleton.id,
                //name:  skeleton.baseName,
                value: skeleton.id,
                type:  'checkbox',
                checked:false
        })
        .click( function( event )
        {
          skeleton.text_visible = $('#skeletontext' + widgetID + '-' + skeleton.id).is(':checked');
          table.notifyLink(skeleton);
        } )
  ));

  var td = $(document.createElement("td"));
  td.append(
    $(document.createElement("button")).attr({
      value: 'P'
    })
      .click( function( event )
      {
        skeleton.property_dialog();
      })
      .text('P')
  );
  td.append(
    $(document.createElement("button")).attr({
      id: 'skeletonaction-changecolor-' + widgetID + '-' + skeleton.id,
      value: 'color'
    })
      .click( function( event )
      {
        // Select the inner div, which will contain the color wheel
        var sel = $('#color-wheel' + widgetID + '-' + skeleton.id + ' .colorwheel' + skeleton.id);
        if (skeleton.cw) {
          delete skeleton.cw;
          $('#color-wheel' + widgetID + '-' + skeleton.id).hide();
          sel.empty();
        } else {
          var cw = Raphael.colorwheel(sel[0], 150);
          cw.color('#' + skeleton.color.getHexString());
          cw.onchange(function(color) {
            skeleton.color = new THREE.Color().setRGB(parseInt(color.r) / 255.0, parseInt(color.g) / 255.0, parseInt(color.b) / 255.0);
            table.gui.update_skeleton_color_button(skeleton);
            table.notifyLink(skeleton);
          });
          skeleton.cw = cw;
          $('#color-wheel' + widgetID + '-' + skeleton.id).show();
        }
      })
      .text('color')
      .css("background-color", '#' + skeleton.color.getHexString())
  );
  td.append(
    $('<div id="color-wheel' + widgetID + '-' + skeleton.id + '"><div class="colorwheel' + skeleton.id + '"></div></div>')
  );
  td.append(
    $(document.createElement("button")).attr({
      value: 'Info'
    })
      .click( function( event )
      {
        skeleton.skeleton_info();
      })
      .text('Info')
  );

  rowElement.append( td );

  $('#skeleton-table' + widgetID + ' > tbody:last').append( rowElement );
 
  if (skeleton.id === this.table.selected_skeleton_id) {
    this.table.highlight(skeleton.id);
  }
};

SelectionTable.prototype.selectSkeletonById = function(id) {
  if (id in this.skeleton_ids) {
    this.selectSkeleton(this.skeletons[this.skeleton_ids[id]], true);
  }
};

SelectionTable.prototype.selectSkeleton = function( skeleton, vis ) {
  $('#skeletonshow' + this.widgetID + '-' + skeleton.id).attr('checked', vis);
  skeleton.selected = vis;
  this.notifyLink(skeleton);
};


SelectionTable.prototype.save_skeleton_list = function() {
  var shortname = prompt('Short name reference for skeleton list?');
  if (!shortname) return;
  shortname = shortname.trim();
  if (0 === shortname.length) return; // can't save a no-name list
  var self = this;
  jQuery.ajax({
    url: django_url + project.id + '/skeletonlist/save',
    data: { 
      shortname: shortname,
      skeletonlist: self.getSelectedSkeletons()
    },
    type: "POST",
    dataType: "json",
    success: function () {}
  });
};

SelectionTable.prototype.load_skeleton_list = function() {
  var shortname = prompt('Short name reference?');
  if (!shortname) return;
  var self = this;
  jQuery.ajax({
    url: django_url + project.id + '/skeletonlist/load',
    data: { shortname: shortname },
    type: "POST",
    dataType: "json",
    success: function ( data ) {
      self.addSkeletons(data['skeletonlist']);
    }
  });
};

SelectionTable.prototype.usercolormap_dialog = function() {
  var dialog = document.createElement('div');
  dialog.setAttribute("id", "user-colormap-dialog");
  dialog.setAttribute("title", "User colormap");

  var tab = document.createElement('table');
  tab.setAttribute("id", "usercolormap-table");
  tab.innerHTML =
      '<thead>' +
        '<tr>' +
          '<th>login</th>' +
          '<th>name</th>' +
          '<th>color</th>' +
        '</tr>' +
      '</thead>' +
      '<tbody></tbody>';
  dialog.appendChild(tab);

  $(dialog).dialog({
    height: 440,
    width: 340,
    modal: false,
    dialogClass: "no-close",
    buttons: {
      "OK": function() {
        $(this).dialog("close");
      }
    },
    close: function(event, ui) {
      $('#user-colormap-dialog').remove();
    }
  });

  var users = User.all();
  for (var userID in users) {
    if (users.hasOwnProperty(userID) && userID !== "-1") {
      var user = users[userID];
      var rowElement = $('<tr/>');
      rowElement.append( $('<td/>').text( user.login ) );
      rowElement.append( $('<td/>').text( user.fullName ) );
      rowElement.append( $('<div/>').css('width', '100px').css('height', '20px').css('background-color', '#' + user.color.getHexString()) );
      $('#usercolormap-table > tbody:last').append( rowElement );
    }
  }
};

SelectionTable.prototype.measure = function() {
  var skids = this.getSelectedSkeletons();
  if (0 === skids.length) return;
  var self = this;
  requestQueue.register(django_url + project.id + '/skeletons/measure', "POST",
    {skeleton_ids: skids},
    function(status, text) {
      if (200 !== status) return;
      var json = $.parseJSON(text);
      if (json.error) {
        alert(json.error);
        return;
      }
      SkeletonMeasurementsTable.populate(json.map(function(row) {
        var model = self.skeletons[self.skeleton_ids[row[0]]];
        row.unshift(model.baseName + ' #' + model.id);
        return row;
      }));
    });
};


/** credit: http://stackoverflow.com/questions/638948/background-color-hex-to-javascript-variable-jquery */
SelectionTable.prototype._rgb2hex = function(rgb) {
  rgb = rgb.match(/^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/);
  var hex = function(x) {
    return ("0" + parseInt(x).toString(16)).slice(-2);
  }
  return "#" + hex(rgb[1]) + hex(rgb[2]) + hex(rgb[3]);
};

SelectionTable.prototype._componentToHex = function(c) {
  var hex = c.toString(16);
  return hex.length === 1 ? "0" + hex : hex;
};

SelectionTable.prototype._rgbarray2hex = function(rgb) {
  return "#" + this._componentToHex(rgb[0]) + this._componentToHex(rgb[1]) + this._componentToHex(rgb[2]);
};

SelectionTable.prototype._hex2rgb = function(hex) {
  var result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return [parseInt(result[1], 16), parseInt(result[2], 16), parseInt(result[3], 16)];
};
